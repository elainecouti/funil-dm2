#!/usr/bin/env python3
"""
Gerador de Funil — DM2 todas as unidades
funil.sevenmidas.com.br/[slug]/
Métricas corretas: agend / leads com diálogo (não /total)
"""
import json, os, re, sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ── Unidades configuradas ─────────────────────────────────────────────────────
UNITS = [
    {
        "slug": "cuiaba",
        "nome": "Cuiabá",
        "estado": "MT",
        "server": "s18.zapclinic.app",
        "email": "dm2cuaiaba@zapclinic.com",
        "password": "123456",
        "meta": 20,
        "periodo_dias": 37,
    },
    # Adicionar outras unidades aqui:
    # {
    #     "slug": "vitoria",
    #     "nome": "Vitória",
    #     "estado": "ES",
    #     "server": "s17.zapclinic.app",
    #     "email": "dm2vitoria@zapclinic.com",
    #     "password": "...",
    #     "meta": 20,
    #     "periodo_dias": 37,
    # },
]

# ── ZapClinic helpers ─────────────────────────────────────────────────────────
def _clean(html):
    return re.sub(r"<[^>]+>", "", html).strip()

def zapclinic_login(server, email, password):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9",
    })
    resp = session.post(f"https://{server}/login", data={
        "email": email, "password": password, "browser_session": "",
    }, allow_redirects=True, timeout=20)
    ok = "/dashboard" in resp.url or "/chats" in resp.url
    if not ok:
        print(f"  [WARN] Login pode ter falhado: {resp.url}")
    return session

def fetch_leads(session, server, date_from, date_to):
    base = f"https://{server}"
    session.get(f"{base}/reports/add", timeout=15)
    form = {
        "report_name": "", "not_delegated": "", "report_chat_name_contains": "",
        "report_sign_up_within_days": "",
        "report_sign_up_from": date_from, "report_sign_up_to": date_to,
        "report_days_with_msg": "", "report_days_without_msg": "",
        "report_days_without_msg_in": "", "report_days_without_msg_out": "",
        "send_by_email": "", "send_by_whatsapp": "",
        "report_include_tags_rule": "", "report_exclude_tags_rule": "",
        "report_chat_status": "", "report_is_archived": "",
        "report_has_pending_action": "", "report_has_unread_msg": "",
        "report_has_scheduled_msg": "", "report_bot_status": "",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{base}/reports/add",
    }
    leads = []
    for page in range(1, 50):
        try:
            resp = session.post(f"{base}/reports/generate/{page}", data=form, headers=headers, timeout=90)
        except Exception:
            break
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", resp.text, re.DOTALL)
        data_rows = [r for r in rows if "<td" in r]
        if not data_rows:
            break
        for row in data_rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) < 3:
                continue
            nome = _clean(cells[0]).split("\n")[0].strip()
            if not nome or nome.upper() in ("NOME", "NOME DO CHAT", "NAME"):
                continue
            whatsapp = _clean(cells[1]).split("\n")[0].strip() if len(cells) > 1 else ""
            cadastro = _clean(cells[2]).split("\n")[0].strip() if len(cells) > 2 else ""
            leads.append({"nome": nome, "whatsapp": whatsapp, "cadastro": cadastro})
    return leads

def fetch_dialogs(session, server, date_from, date_to):
    base = f"https://{server}"
    session.get(f"{base}/reports/dialogs", timeout=15)
    # created_from/to = data do diálogo (NÃO report_sign_up_from/to que é data do cadastro)
    form = {
        "report_name": "", "not_delegated": "",
        "created_from": date_from, "created_to": date_to,
        "report_chat_status": "", "report_include_tags_rule": "",
        "report_exclude_tags_rule": "",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{base}/reports/dialogs",
    }
    dialogs = []
    for page in range(1, 50):
        try:
            resp = session.post(f"{base}/reports/dialogs/generate/{page}", data=form, headers=headers, timeout=90)
        except Exception:
            break
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", resp.text, re.DOTALL)
        data_rows = [r for r in rows if "<td" in r]
        if not data_rows:
            break
        for row in data_rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 4:
                dialogs.append({
                    "dialogo": _clean(cells[0]).strip(),  # texto completo, não só 1ª linha
                    "lead": _clean(cells[1]).split("\n")[0].strip(),
                    "whatsapp": _clean(cells[2]).split("\n")[0].strip() if len(cells) > 2 else "",
                    "data": _clean(cells[4]).split("\n")[0].strip() if len(cells) > 4 else "",
                })
    return dialogs

RE_AGENDOU   = re.compile(r"\bagendou\b", re.I)
RE_REAGENDOU = re.compile(r"\breagendou\b", re.I)

def _norm_wpp(wpp):
    return re.sub(r"\D", "", wpp)[-11:] if wpp else ""

def _parse_data(s):
    """Converte 'DD/MM/YYYY às HHhMMm' em datetime para ordenação correta."""
    try:
        return datetime.strptime(s[:10], "%d/%m/%Y")
    except Exception:
        return datetime.min

def detect_agendados(dialogs):
    """Retorna (agendados, reagendados) como listas separadas de dicts únicos por lead."""
    agendados, reagendados = [], []
    seen_ag, seen_re = set(), set()
    for d in sorted(dialogs, key=lambda x: _parse_data(x.get("data", "")), reverse=True):
        texto = d.get("dialogo", "")
        key = _norm_wpp(d.get("whatsapp")) or d.get("lead", "")
        if not key:
            continue
        if RE_REAGENDOU.search(texto) and key not in seen_re:
            seen_re.add(key)
            reagendados.append(d)
        if RE_AGENDOU.search(texto) and key not in seen_ag:
            seen_ag.add(key)
            agendados.append(d)
    return agendados, reagendados

def calcular_metricas(leads, dialogs, agendados, reagendados, meta_pct):
    total_leads       = len(leads)
    total_dialogs     = len(dialogs)
    total_reagendados = len(reagendados)
    # Total = agendamentos + reagendamentos (eventos, não leads únicos)
    total_agendados = len(agendados) + len(reagendados)
    wpp_leads   = {_norm_wpp(l.get("whatsapp")) for l in leads   if l.get("whatsapp")}
    wpp_dialogs = {_norm_wpp(d.get("whatsapp")) for d in dialogs if d.get("whatsapp")}
    wpp_leads.discard(""); wpp_dialogs.discard("")
    leads_com_dialogo = len(wpp_leads & wpp_dialogs) if wpp_leads and wpp_dialogs else total_dialogs
    sem_resposta      = max(0, total_leads - leads_com_dialogo)
    tx_total          = round(total_agendados / total_leads * 100, 1) if total_leads else 0
    tx_dialogo        = round(total_agendados / leads_com_dialogo * 100, 1) if leads_com_dialogo else 0
    agend_necessarios = round(leads_com_dialogo * meta_pct / 100)
    gap = max(0, agend_necessarios - total_agendados)
    return {
        "total_leads": total_leads, "total_dialogs": total_dialogs,
        "leads_com_dialogo": leads_com_dialogo, "sem_resposta": sem_resposta,
        "total_agendados": total_agendados, "total_reagendados": total_reagendados,
        "tx_total": tx_total, "tx_dialogo": tx_dialogo,
        "meta_pct": meta_pct, "agend_necessarios": agend_necessarios,
        "gap": gap, "agendados_lista": agendados[:25],
    }

# ── HTML ──────────────────────────────────────────────────────────────────────
def gerar_html(unit, m, date_from, date_to, atualizado):
    tx_cor = "#22c55e" if m["tx_dialogo"] >= m["meta_pct"] else (
             "#f59e0b" if m["tx_dialogo"] >= m["meta_pct"] * 0.7 else "#ef4444")
    barra_pct = min(100, round(m["tx_dialogo"] / m["meta_pct"] * 100))
    gap_label = f"faltam {m['gap']} agendamentos pra meta" if m["gap"] > 0 else "✓ meta atingida"

    rows = ""
    for i, d in enumerate(m["agendados_lista"], 1):
        rows += f"<tr><td>{i}</td><td>{d.get('lead','—')}</td><td>{d.get('data','—')}</td></tr>"

    nome_uf = f"{unit['nome']} — {unit['estado']}"

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#184341">
<title>Funil · DM2 {unit['nome']}</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800&family=Outfit:wght@700;800&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
html{{scroll-behavior:smooth}}
body{{background:#F7F8FA;color:#184341;font-family:'Montserrat',system-ui,sans-serif;font-size:15px;min-height:100vh}}

/* TOPBAR */
.topbar{{background:#184341;display:flex;justify-content:space-between;align-items:center;padding:0 18px;height:52px;position:sticky;top:0;z-index:50}}
.logo{{display:flex;align-items:center;gap:5px;text-decoration:none}}
.logo svg{{width:28px;height:28px}}
.logo-text{{font-family:'Outfit',sans-serif;font-size:19px;font-weight:800;color:#fff;letter-spacing:-.5px}}
.logo-midas{{background:linear-gradient(135deg,#00E87B,#00B8D4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.live{{display:flex;align-items:center;gap:6px}}
.dot{{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:blink 2s infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.live span{{font-size:11px;color:rgba(255,255,255,.55)}}

/* WRAP */
.wrap{{max-width:520px;margin:0 auto;padding:18px 14px 48px}}

/* HERO */
.hero{{margin-bottom:18px}}
.badge{{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#00A19A;background:#e6faf8;padding:3px 10px;border-radius:20px;margin-bottom:8px}}
.hero h1{{font-size:24px;font-weight:800;line-height:1.15;margin-bottom:4px}}
.hero p{{font-size:12px;color:#7a7a7a}}

/* METRIC HERO */
.mhero{{background:#184341;border-radius:18px;padding:20px 18px;margin-bottom:14px;color:#fff;position:relative;overflow:hidden}}
.mhero::before{{content:'';position:absolute;top:-30px;right:-30px;width:120px;height:120px;border-radius:50%;background:rgba(255,255,255,.04)}}
.mhero-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:rgba(255,255,255,.45);margin-bottom:3px}}
.mhero-val{{font-size:52px;font-weight:800;line-height:1;color:{tx_cor};margin-bottom:2px}}
.mhero-sub{{font-size:11px;color:rgba(255,255,255,.55);margin-bottom:14px}}
.prog-bg{{background:rgba(255,255,255,.12);border-radius:8px;height:7px;overflow:hidden;margin-bottom:5px}}
.prog-fill{{height:100%;border-radius:8px;background:{tx_cor};width:{barra_pct}%}}
.prog-labels{{display:flex;justify-content:space-between;font-size:10px;color:rgba(255,255,255,.4)}}
.gap-chip{{display:inline-block;margin-top:10px;font-size:11px;font-weight:600;padding:4px 12px;border-radius:20px;background:rgba(255,255,255,.1);color:rgba(255,255,255,.75)}}

/* EXPLAIN BOX */
.explain{{background:#eff6ff;border-left:4px solid #3b82f6;border-radius:12px;padding:13px 14px;margin-bottom:14px;font-size:12px;color:#1e40af;line-height:1.6}}
.explain strong{{font-size:13px}}

/* CARDS 2x2 */
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}}
.card{{background:#fff;border-radius:13px;padding:14px 15px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.card-lbl{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#aaa;margin-bottom:3px}}
.card-val{{font-size:26px;font-weight:800;color:#184341;line-height:1}}
.card-sub{{font-size:11px;color:#7a7a7a;margin-top:2px}}
.card.g{{border-left:4px solid #22c55e}}
.card.b{{border-left:4px solid #00B8D4}}
.card.p{{border-left:4px solid #9333ea}}
.card.y{{border-left:4px solid #f59e0b}}
.card.r{{border-left:4px solid #ef4444}}
.card.n{{border-left:4px solid #d1d5db}}

/* FUNIL BARS */
.funil{{background:#fff;border-radius:13px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:14px}}
.funil-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#184341;margin-bottom:12px}}
.fstep{{display:grid;grid-template-columns:100px 1fr 36px;align-items:center;gap:8px;margin-bottom:8px}}
.fstep-lbl{{font-size:11px;font-weight:600;color:#555;text-align:right}}
.fbar-bg{{background:#f0f0f0;border-radius:6px;height:26px;overflow:hidden}}
.fbar{{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:8px;font-size:10px;font-weight:700;color:#fff;min-width:26px}}
.fstep-n{{font-size:12px;font-weight:800;color:#184341;text-align:right}}

/* ALERT */
.alert{{border-radius:11px;padding:12px 13px;font-size:12px;margin-bottom:14px;display:flex;gap:8px;line-height:1.6}}
.ay{{background:#fffbeb;border-left:4px solid #f59e0b;color:#92400e}}
.ag{{background:#f0fdf4;border-left:4px solid #22c55e;color:#166534}}

/* TABLE */
.sec-title{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#184341;margin:18px 0 8px;padding-bottom:5px;border-bottom:2px solid #e8e8e8}}
.tbl-wrap{{background:#fff;border-radius:13px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#184341;color:#fff;padding:9px 12px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}}
td{{padding:9px 12px;border-bottom:1px solid #f0f0f0;vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
td:first-child{{font-size:11px;font-weight:700;color:#aaa;width:28px}}
td:last-child{{font-size:10px;color:#7a7a7a;white-space:nowrap}}

/* BACK LINK */
.back{{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;color:#00A19A;text-decoration:none;margin-bottom:16px}}

.footer{{text-align:center;padding:20px 16px 12px;font-size:10px;color:#ccc}}

@media(max-width:400px){{
  .hero h1{{font-size:21px}}
  .mhero-val{{font-size:44px}}
  .fstep{{grid-template-columns:90px 1fr 32px}}
}}
</style>
</head>
<body>

<div class="topbar">
  <a class="logo" href="/">
    <svg viewBox="0 0 24 24" fill="none"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00E87B"/><stop offset="100%" stop-color="#00B8D4"/></linearGradient></defs><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z" fill="url(#lg)"/></svg>
    <span class="logo-text">seven <span class="logo-midas">midas</span></span>
  </a>
  <div class="live"><div class="dot"></div><span>Atualizado {atualizado}</span></div>
</div>

<div class="wrap">

  <a class="back" href="/">← Todas as unidades</a>

  <div class="hero">
    <div class="badge">ZapClinic · Ao vivo</div>
    <h1>Funil DM2 {unit['nome']}</h1>
    <p>{date_from} a {date_to} · Atualiza a cada 3h</p>
  </div>

  <div class="mhero">
    <div class="mhero-label">Agendamento / leads com diálogo</div>
    <div class="mhero-val">{m['tx_dialogo']}%</div>
    <div class="mhero-sub">Métrica correta · meta: {m['meta_pct']}%</div>
    <div class="prog-bg"><div class="prog-fill"></div></div>
    <div class="prog-labels"><span>0%</span><span>Meta {m['meta_pct']}%</span></div>
    <span class="gap-chip">{gap_label}</span>
  </div>

  <div class="explain">
    De {m['total_leads']} leads gerados, <strong>{m['sem_resposta']}</strong> nunca responderam — não entram no cálculo.<br>
    Denominador correto: <strong>{m['leads_com_dialogo']} leads com diálogo</strong>.<br>
    Taxa sobre total: <strong>{m['tx_total']}%</strong> (referência) · Taxa real: <strong>{m['tx_dialogo']}%</strong>
  </div>

  <div class="cards">
    <div class="card b">
      <div class="card-lbl">Leads totais</div>
      <div class="card-val">{m['total_leads']}</div>
      <div class="card-sub">no período</div>
    </div>
    <div class="card g">
      <div class="card-lbl">Com diálogo</div>
      <div class="card-val">{m['leads_com_dialogo']}</div>
      <div class="card-sub">responderam</div>
    </div>
    <div class="card p">
      <div class="card-lbl">Agendados</div>
      <div class="card-val">{m['total_agendados']}</div>
      <div class="card-sub">no período</div>
    </div>
    <div class="card b">
      <div class="card-lbl">Reagendados</div>
      <div class="card-val">{m['total_reagendados']}</div>
      <div class="card-sub">remarcararam consulta</div>
    </div>
    <div class="card {'r' if m['gap'] > 0 else 'g'}">
      <div class="card-lbl">Gap meta {m['meta_pct']}%</div>
      <div class="card-val">{"+" + str(m['gap']) if m['gap'] > 0 else "✓"}</div>
      <div class="card-sub">{"faltando" if m['gap'] > 0 else "atingida"}</div>
    </div>
    <div class="card y">
      <div class="card-lbl">Sem resposta</div>
      <div class="card-val">{m['sem_resposta']}</div>
      <div class="card-sub">nunca responderam</div>
    </div>
    <div class="card n">
      <div class="card-lbl">Taxa / total</div>
      <div class="card-val">{m['tx_total']}%</div>
      <div class="card-sub">referência apenas</div>
    </div>
  </div>

  <div class="funil">
    <div class="funil-title">Funil visual</div>
    <div class="fstep">
      <div class="fstep-lbl">Leads gerados</div>
      <div class="fbar-bg"><div class="fbar" style="width:100%;background:#184341">{m['total_leads']}</div></div>
      <div class="fstep-n">{m['total_leads']}</div>
    </div>
    <div class="fstep">
      <div class="fstep-lbl">Com diálogo</div>
      <div class="fbar-bg"><div class="fbar" style="width:{round(m['leads_com_dialogo']/m['total_leads']*100) if m['total_leads'] else 0}%;background:#00A19A">{m['leads_com_dialogo']}</div></div>
      <div class="fstep-n">{m['leads_com_dialogo']}</div>
    </div>
    <div class="fstep">
      <div class="fstep-lbl">Agendados</div>
      <div class="fbar-bg"><div class="fbar" style="width:{round(m['total_agendados']/m['total_leads']*100) if m['total_leads'] else 0}%;background:#9333ea">{m['total_agendados']}</div></div>
      <div class="fstep-n">{m['total_agendados']}</div>
    </div>
    <div class="fstep">
      <div class="fstep-lbl">Meta ({m['meta_pct']}%)</div>
      <div class="fbar-bg"><div class="fbar" style="width:{round(m['agend_necessarios']/m['total_leads']*100) if m['total_leads'] else 0}%;background:#f59e0b;opacity:.6">{m['agend_necessarios']}</div></div>
      <div class="fstep-n" style="color:#f59e0b">{m['agend_necessarios']}</div>
    </div>
  </div>

  {'<div class="alert ag"><span>✅</span><div><strong>Meta atingida!</strong> ' + str(m['tx_dialogo']) + '% ≥ ' + str(m['meta_pct']) + '%</div></div>' if m['gap'] == 0 else '<div class="alert ay"><span>⚠️</span><div>Faltam <strong>' + str(m['gap']) + ' agendamentos</strong> para atingir ' + str(m['meta_pct']) + '% sobre leads com diálogo.</div></div>'}

  <div class="sec-title">Últimos agendamentos — {m['total_agendados']} total</div>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>#</th><th>Lead</th><th>Agendado em</th></tr></thead>
      <tbody>{rows if rows else '<tr><td colspan="3" style="text-align:center;color:#aaa;padding:16px">Nenhum agendamento detectado</td></tr>'}</tbody>
    </table>
  </div>

</div>

<div class="footer">Seven Midas Marketing · DM2 {unit['nome']} · {atualizado}</div>
</body>
</html>"""


def gerar_index(units_ok, atualizado):
    """Página inicial: lista de todas as unidades com funil."""
    cards = ""
    for u in units_ok:
        m = u["metricas"]
        cor = "#22c55e" if m["tx_dialogo"] >= m["meta_pct"] else (
              "#f59e0b" if m["tx_dialogo"] >= m["meta_pct"] * 0.7 else "#ef4444")
        cards += f"""
        <a class="ucard" href="/{u['slug']}/">
          <div class="ucard-top">
            <div>
              <div class="ucard-nome">{u['nome']}</div>
              <div class="ucard-estado">{u['estado']}</div>
            </div>
            <div class="ucard-rate" style="color:{cor}">{m['tx_dialogo']}%</div>
          </div>
          <div class="ucard-bar-bg">
            <div class="ucard-bar" style="width:{min(100, round(m['tx_dialogo']/m['meta_pct']*100))}%;background:{cor}"></div>
          </div>
          <div class="ucard-bottom">
            <span>{m['total_leads']} leads</span>
            <span>{m['total_agendados']} agendados</span>
            <span>meta {m['meta_pct']}%</span>
          </div>
        </a>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="theme-color" content="#184341">
<title>Seven · Funil DM2</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800&family=Outfit:wght@700;800&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
body{{background:#F7F8FA;color:#184341;font-family:'Montserrat',system-ui,sans-serif}}
.topbar{{background:#184341;display:flex;justify-content:space-between;align-items:center;padding:0 18px;height:52px;position:sticky;top:0;z-index:50}}
.logo{{display:flex;align-items:center;gap:5px;text-decoration:none}}
.logo svg{{width:28px;height:28px}}
.logo-text{{font-family:'Outfit',sans-serif;font-size:19px;font-weight:800;color:#fff;letter-spacing:-.5px}}
.logo-midas{{background:linear-gradient(135deg,#00E87B,#00B8D4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.live{{display:flex;align-items:center;gap:6px}}
.dot{{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:blink 2s infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.live span{{font-size:11px;color:rgba(255,255,255,.55)}}
.wrap{{max-width:520px;margin:0 auto;padding:18px 14px 48px}}
.hero{{margin-bottom:18px}}
.hero h1{{font-size:22px;font-weight:800;margin-bottom:3px}}
.hero p{{font-size:12px;color:#7a7a7a}}
.grid{{display:flex;flex-direction:column;gap:10px}}
.ucard{{background:#fff;border-radius:14px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.07);text-decoration:none;color:#184341;display:block;transition:box-shadow .2s}}
.ucard:hover{{box-shadow:0 4px 12px rgba(0,0,0,.1)}}
.ucard-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
.ucard-nome{{font-size:15px;font-weight:800}}
.ucard-estado{{font-size:11px;color:#7a7a7a;margin-top:2px}}
.ucard-rate{{font-size:28px;font-weight:800;line-height:1}}
.ucard-bar-bg{{background:#f0f0f0;border-radius:6px;height:6px;overflow:hidden;margin-bottom:8px}}
.ucard-bar{{height:100%;border-radius:6px;transition:width .5s}}
.ucard-bottom{{display:flex;justify-content:space-between;font-size:10px;color:#aaa;font-weight:600;text-transform:uppercase;letter-spacing:.04em}}
.footer{{text-align:center;padding:20px 16px;font-size:10px;color:#ccc}}
</style>
</head>
<body>
<div class="topbar">
  <a class="logo" href="/">
    <svg viewBox="0 0 24 24" fill="none"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00E87B"/><stop offset="100%" stop-color="#00B8D4"/></linearGradient></defs><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z" fill="url(#lg)"/></svg>
    <span class="logo-text">seven <span class="logo-midas">midas</span></span>
  </a>
  <div class="live"><div class="dot"></div><span>{atualizado}</span></div>
</div>
<div class="wrap">
  <div class="hero">
    <h1>Funil DM2</h1>
    <p>Agendamentos / leads com diálogo · meta 20% · atualiza 3h</p>
  </div>
  <div class="grid">{cards}</div>
</div>
<div class="footer">Seven Midas Marketing · {atualizado}</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    hoje = datetime.now()
    atualizado = hoje.strftime("%d/%m/%Y %H:%M")
    units_ok = []

    for unit in UNITS:
        slug = unit["slug"]
        date_to   = hoje.strftime("%Y-%m-%d")
        date_from = (hoje - timedelta(days=unit["periodo_dias"])).strftime("%Y-%m-%d")
        print(f"\n[{slug}] {date_from} → {date_to}")

        try:
            session = zapclinic_login(unit["server"], unit["email"], unit["password"])
            print(f"[{slug}] Login OK")
            leads   = fetch_leads(session, unit["server"], date_from, date_to)
            print(f"[{slug}] {len(leads)} leads")
            dialogs = fetch_dialogs(session, unit["server"], date_from, date_to)
            print(f"[{slug}] {len(dialogs)} diálogos")
            agendados, reagendados = detect_agendados(dialogs)
            print(f"[{slug}] {len(agendados)} agendamentos · {len(reagendados)} reagendamentos")
            m = calcular_metricas(leads, dialogs, agendados, reagendados, unit["meta"])
            unit["metricas"] = m
            units_ok.append(unit)

            out = Path(slug)
            out.mkdir(exist_ok=True)
            (out / "index.html").write_text(
                gerar_html(unit, m, date_from, date_to, atualizado), encoding="utf-8"
            )
            print(f"[{slug}] ✅ {slug}/index.html")
        except Exception as e:
            print(f"[{slug}] ❌ {e}")

    # Index geral
    Path("index.html").write_text(gerar_index(units_ok, atualizado), encoding="utf-8")
    print("\n✅ index.html gerado")

if __name__ == "__main__":
    main()
