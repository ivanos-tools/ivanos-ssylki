#!/usr/bin/env python3
"""gasitel.py — гасит одноразовые ссылки установки в ivanos-tools/ivanos-ssylki.

Слово владельца 03-10: «ссылка действительна один раз, то есть одна установка и всё».
Ссылка = релиз `z-<id>` с зашифрованным пакетом. В теле релиза — открытые сведения
(без секретов): какие ключи развёртывания заведены этой установке и до какого срока
ссылка живёт.

🔴 Чем узнаём «ссылка использована» — `last_used` ключа развёртывания установки, а НЕ
`download_count`. Проба 0, 03-10: ассет скачан анонимно curl в 09:47:51 UTC —
download_count = 0 и через 5 минут; ключ развёртывания, тронутый `git ls-remote`
в 09:53:31, показал last_used = 09:53:33 уже через ≤12 с. download_count — только
запасной признак: вырос — тоже гасим.

Гасит:
  · ключ установки использован (last_used не пуст) или снят (404) → удалить релиз и тег;
  · download_count ≥ 1                                            → то же;
  · срок вышел, ключ не тронут (не запасная ссылка)               → удалить релиз, тег
    и ключи установки (если токену это дано; нет — сказать, ключ снимет машина дома).
Ключ ИСПОЛЬЗОВАННОЙ ссылки не трогает: им живёт поставленная машина.

Где бежит: GitHub Actions (расписание, от Contabo и от дома не зависит) и машина дома
(запасной гаситель). Код один.

Окружение:
  GH_TOKEN_SSYLKI   — запись в ivanos-ssylki (в Actions — встроенный GITHUB_TOKEN)
  GH_TOKEN_KLYUCHI  — чтение ключей развёртывания (Administration: read на репозитории
                      инструментов). Нет его — гасит только по сроку и download_count,
                      и говорит об этом в выводе.
  SSYLKI_REPO       — по умолчанию ivanos-tools/ivanos-ssylki
  GASITEL_OTMETKA   — файл, куда пишется время прогона (для датчика на машине)
  GASITEL_SUHO=1    — только показать, ничего не удалять

Выход: 0 — прошёл; 1 — были ошибки API (датчик это видит).
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
REPO = os.environ.get("SSYLKI_REPO", "ivanos-tools/ivanos-ssylki")
T_SSYLKI = os.environ.get("GH_TOKEN_SSYLKI", "")
T_KLYUCHI = os.environ.get("GH_TOKEN_KLYUCHI", "")
SUHO = os.environ.get("GASITEL_SUHO") == "1"
OSHIBKI = 0


def zapros(metod, put, token, telo=None):
    """(код, json|None). Токен не печатается никогда."""
    zag = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        zag["Authorization"] = "Bearer " + token
    dannye = json.dumps(telo).encode() if telo is not None else None
    r = urllib.request.Request(API + put, data=dannye, method=metod, headers=zag)
    try:
        with urllib.request.urlopen(r, timeout=20) as o:
            t = o.read()
            return o.status, (json.loads(t) if t else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e)


def skazat(s):
    print(s, flush=True)


def udalit_ssylku(rel, prichina):
    global OSHIBKI
    teg = rel["tag_name"]
    if SUHO:
        skazat(f"  [сухо] погасил бы {teg}: {prichina}")
        return
    k, _ = zapros("DELETE", f"/repos/{REPO}/releases/{rel['id']}", T_SSYLKI)
    k2, _ = zapros("DELETE", f"/repos/{REPO}/git/refs/tags/{teg}", T_SSYLKI)
    if k == 204:
        skazat(f"  🔥 погашена {teg}: {prichina} (релиз 204, тег {k2})")
    else:
        OSHIBKI += 1
        skazat(f"  🔴 НЕ погашена {teg}: {prichina} — удаление релиза ответило {k}")


def snyat_klyuchi(klyuchi, teg):
    global OSHIBKI
    for kl in klyuchi:
        if SUHO:
            skazat(f"  [сухо] снял бы ключ {kl['repo']}#{kl['id']}")
            continue
        k, _ = zapros("DELETE", f"/repos/{kl['repo']}/keys/{kl['id']}", T_KLYUCHI)
        if k in (204, 404):
            skazat(f"  ключ {kl['repo']}#{kl['id']} снят ({k})")
        else:
            skazat(f"  ⚠ ключ {kl['repo']}#{kl['id']} снять не дано ({k}) — снимет машина дома: {teg}")


def razobrat(rel):
    """Сведения ссылки из тела релиза; None — релиз не наш."""
    if not rel["tag_name"].startswith("z-"):
        return None
    try:
        return json.loads(rel.get("body") or "")
    except ValueError:
        return {}


def main():
    global OSHIBKI
    seychas = dt.datetime.now(dt.timezone.utc)
    k, reliz = zapros("GET", f"/repos/{REPO}/releases?per_page=100", T_SSYLKI)
    if k != 200:
        skazat(f"🔴 список релизов {REPO} не получен: {k}")
        return 1
    nashi = [(r, razobrat(r)) for r in reliz]
    nashi = [(r, s) for r, s in nashi if s is not None]
    skazat(f"гаситель {seychas:%Y-%m-%dT%H:%M:%SZ}: ссылок {len(nashi)}"
           + ("" if T_KLYUCHI else " · ⚠ нет GH_TOKEN_KLYUCHI — гашу только по сроку и скачиваниям"))
    for rel, sv in nashi:
        teg = rel["tag_name"]
        if not sv:
            udalit_ssylku(rel, "тело релиза не разобрано — ссылка без сведений не живёт")
            continue
        klyuchi = sv.get("klyuchi", [])
        # 1. ключ установки тронут или снят
        tronut = None
        for kl in klyuchi:
            if not T_KLYUCHI:
                break
            kk, d = zapros("GET", f"/repos/{kl['repo']}/keys/{kl['id']}", T_KLYUCHI)
            if kk == 404:
                tronut = f"ключ {kl['repo']}#{kl['id']} снят"
                break
            if kk != 200:
                OSHIBKI += 1
                skazat(f"  🔴 {teg}: ключ {kl['repo']}#{kl['id']} не прочитан ({kk})")
                continue
            if d.get("last_used"):
                tronut = f"ключ {kl['repo']}#{kl['id']} использован {d['last_used']}"
                break
        if tronut:
            udalit_ssylku(rel, tronut)
            continue
        # 2. запасной признак — скачивание
        skach = sum(a.get("download_count", 0) for a in rel.get("assets", []))
        if skach >= 1:
            udalit_ssylku(rel, f"скачана {skach} раз(а)")
            continue
        # 3. срок
        srok = sv.get("srok_do")
        if srok and not sv.get("zapasnaya"):
            if seychas >= dt.datetime.fromisoformat(srok.replace("Z", "+00:00")):
                udalit_ssylku(rel, f"срок вышел {srok}, не использована")
                if T_KLYUCHI:
                    snyat_klyuchi(klyuchi, teg)
                continue
        skazat(f"  жива {teg}: {sv.get('vid','?')}, до {srok or 'без срока'}")
    otm = os.environ.get("GASITEL_OTMETKA")
    if otm and not SUHO:
        with open(otm, "w") as f:
            f.write(f"{seychas:%Y-%m-%dT%H:%M:%SZ} ошибок={OSHIBKI}\n")
    return 1 if OSHIBKI else 0


if __name__ == "__main__":
    sys.exit(main())
