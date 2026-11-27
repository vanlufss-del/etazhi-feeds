"""Скачивает фиды из feeds.txt, разбирает квартиры (Авито, Яндекс, Циан) и скачивает планировки.
Результат: out/<имя>.json (квартиры), out/<имя>/plans/<id>.jpg (планировки до 900 px на белом фоне), out/report.txt."""
import io, json, os, re, urllib.request, xml.etree.ElementTree as ET
from PIL import Image

UA = {"User-Agent": "Mozilla/5.0 (feeds snapshot)"}
MAX_PLANS = 60


def get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def local(tag):
    return tag.rsplit('}', 1)[-1].lower()


def kid(el, *names):
    for n in names:
        if el is None:
            return None
        el = next((c for c in el if local(c.tag) == n), None)
    return el


def txt(el, *names):
    k = kid(el, *names)
    return k.text.strip() if k is not None and k.text else None


def num(v):
    try:
        return float(str(v).replace(',', '.'))
    except Exception:
        return None


def is_plan(url, tag=''):
    t, u = (tag or '').lower(), url or ''
    if re.search(r'floor|этаж', t) or '/cat_lev/' in u:
        return False
    return bool(re.search(r'plan|планир', t) or re.search(r'/pla/|planirov|plan', u, re.I))


def avito(root):
    out = []
    for a in root.iter():
        if local(a.tag) != 'ad':
            continue
        cat = (txt(a, 'category') or '').lower()
        if cat and 'кварт' not in cat:
            continue
        imgs = [(i.get('url') or '', i.get('tag') or i.get('name') or '') for i in (kid(a, 'images') or [])]
        plans = [u for u, t in imgs if is_plan(u, t)]
        m = re.search(r'ЖК\s*[«"„“]([^»"“”\n]{2,60})[»"“”]', txt(a, 'description') or '')
        out.append(dict(id=txt(a, 'id'), complex=txt(a, 'complexname') or (m.group(1).strip() if m else None),
                        house=txt(a, 'housingname') or txt(a, 'corpus') or txt(a, 'newdevelopmentid'),
                        rooms=txt(a, 'rooms'), area=num(txt(a, 'square')), floor=txt(a, 'floor'), floors=txt(a, 'floors'),
                        price=num(txt(a, 'price')), finish=txt(a, 'decoration'), status=txt(a, 'status'),
                        plan=plans[0] if plans else None, images=len(imgs)))
    return out


def yandex(root):
    out = []
    for o in root.iter():
        if local(o.tag) != 'offer':
            continue
        imgs = [((i.text or '').strip(), i.get('tag') or '') for i in o if local(i.tag) == 'image']
        plans = [u for u, t in imgs if is_plan(u, t)]
        out.append(dict(id=o.get('internal-id'), complex=txt(o, 'building-name'), house=txt(o, 'building-section'),
                        rooms=txt(o, 'rooms') or ('студия' if txt(o, 'studio') else None), area=num(txt(o, 'area', 'value')),
                        floor=txt(o, 'floor'), floors=txt(o, 'floors-total'), price=num(txt(o, 'price', 'value')),
                        finish=txt(o, 'renovation'), status=None, plan=plans[0] if plans else None, images=len(imgs)))
    return out


def cian(root):
    out = []
    for o in root.iter():
        if local(o.tag) != 'object':
            continue
        jk = kid(o, 'jkschema')
        out.append(dict(id=txt(o, 'externalid'), complex=txt(jk, 'name'), house=txt(jk, 'house', 'name'),
                        rooms=txt(o, 'flatroomscount'), area=num(txt(o, 'totalarea')), floor=txt(o, 'floornumber'),
                        floors=txt(o, 'building', 'floorscount'), price=num(txt(o, 'bargainterms', 'price')),
                        finish=txt(o, 'decoration'), status=txt(o, 'booking', 'status'),
                        plan=txt(o, 'layoutphoto', 'fullurl'), images=0))
    return out


def save_plan(url, path):
    im = Image.open(io.BytesIO(get(url, 40)))
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, 'white')
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert('RGB')
    im.thumbnail((900, 900))
    im.save(path, 'JPEG', quality=82, optimize=True)


def main():
    os.makedirs('out', exist_ok=True)
    report = []
    # ссылки на фиды — из секрета FEEDS (строки «имя ссылка»), чтобы их не было в репозитории
    for line in (os.environ.get('FEEDS') or open('feeds.txt', encoding='utf-8').read()).splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, url = line.split(None, 1)
        try:
            raw = get(url)
        except Exception as e:
            report.append(f'{name}: фид не скачался — {e}')
            continue
        root = ET.fromstring(raw)
        flats = avito(root) or yandex(root) or cian(root)
        # планировки: одна на каждый вариант «ЖК + комнаты + площадь», не больше MAX_PLANS
        os.makedirs(f'out/{name}/plans', exist_ok=True)
        seen, n = {}, 0
        for f in sorted(flats, key=lambda f: (f['complex'] or '', f['rooms'] or '', f['area'] or 0)):
            if not f['plan']:
                continue
            key = (f['complex'], f['rooms'], round(f['area'] or 0, 1))
            if key in seen:
                f['plan_file'] = seen[key]
                continue
            if n >= MAX_PLANS:
                continue
            fn = f"{name}/plans/{re.sub(r'[^0-9A-Za-z_-]', '_', str(f['id']))}.jpg"
            try:
                save_plan(f['plan'], 'out/' + fn)
                seen[key] = fn
                f['plan_file'] = fn
                n += 1
            except Exception as e:
                report.append(f'{name}: планировка {f["id"]} не скачалась — {e}')
        json.dump(flats, open(f'out/{name}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        cx = {}
        for f in flats:
            cx[f['complex']] = cx.get(f['complex'], 0) + 1
        report.append(f'{name}: квартир {len(flats)}, с планировкой {sum(1 for f in flats if f["plan"])}, '
                      f'скачано планировок {n}; ЖК: {cx}')
    open('out/report.txt', 'w', encoding='utf-8').write('\n'.join(report) + '\n')
    print('\n'.join(report))


if __name__ == '__main__':
    main()
