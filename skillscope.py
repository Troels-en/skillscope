#!/usr/bin/env python3
"""skillscope — a one-glance overview of your Claude Code skills.

Scans personal, project, and plugin SKILL.md files, analyses each one's
triggers and risks, and writes a self-contained HTML report.

Zero dependencies. Standard library only. Python 3.9+.

Usage:
    python skillscope.py [--project DIR] [--out FILE] [--context-window N]
                         [--json FILE] [--open]
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

# --- constants ---------------------------------------------------------------

VERSION = "0.2.1"
# Official guideline: keep a SKILL.md body under 500 lines.
BODY_LINE_LIMIT = 500
# Official cap: description + when_to_use is truncated at 1536 chars in the
# skill listing. Going over means triggers get silently cut.
DESC_CHAR_CAP = 1536
# Words that make a description fire very broadly — usually unintended.
# Matched on word boundaries, so "any" does not match "many".
AGGRESSIVE_WORDS = ("any", "every", "always", "all conversations",
                    "before any", "must use", "starting any")
# Plugin-tree path fragments that hold duplicates or non-loaded copies.
PLUGIN_SKIP = ("/cache/", "/.cursor/", "/.cursor-plugin/", "/.windsurf/",
               "/.codex/", "/.codex-plugin/", "/.opencode/", "/.gemini/",
               "/tests/", "/test/", "/template/", "/templates/", "/spec/",
               "/examples/", "/example/")
# Jaccard threshold above which two descriptions are flagged as colliding.
COLLISION_THRESHOLD = 0.18
STOPWORDS = set("""a an the of to and or in on with for from by as is are be
this that use uses used using when whenever should must do not don't user
users claude code skill skills task tasks it its your you via per into out
over after before only also any all each new other than then them their there
here what which who whom whose why how trigger triggers triggering invoke
invoked help helps need needs want wants make makes get gets one two more most
less etc eg ie if else while case cases work works working run runs running
""".split())


# --- frontmatter parsing ------------------------------------------------------

def parse_skill(path):
    """Return a dict describing one SKILL.md, or None if unreadable."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return None

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    front = m.group(1) if m else ""
    body = text[m.end():] if m else text

    fields = _parse_frontmatter(front)
    name = fields.get("name") or os.path.basename(os.path.dirname(path))
    desc = fields.get("description", "").strip()
    when = fields.get("when_to_use", "").strip()
    body_lines = len(body.splitlines()) if body.strip() else 0

    return {
        "name": name,
        "description": desc,
        "when_to_use": when,
        "disable_model_invocation":
            str(fields.get("disable-model-invocation", "")).lower() == "true",
        "user_invocable":
            str(fields.get("user-invocable", "true")).lower() != "false",
        "allowed_tools": fields.get("allowed-tools", ""),
        "body_lines": body_lines,
        "path": path,
    }


def _parse_frontmatter(front):
    """Minimal YAML-ish parser: flat keys, quoted scalars, | and > blocks."""
    fields = {}
    lines = front.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not km:
            i += 1
            continue
        key, raw = km.group(1), km.group(2).strip()
        if raw in ("|", ">", "|-", ">-", "|+", ">+"):
            block = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t"))
                                      or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            fields[key] = " ".join(b for b in block if b).strip()
            continue
        if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
            raw = raw[1:-1]
        fields[key] = raw
        i += 1
    return fields


# --- discovery ----------------------------------------------------------------

def discover(project_dir):
    """Find all SKILL.md files across personal, project, and plugin scopes."""
    home = os.path.expanduser("~")
    found = []
    seen = set()  # realpaths — a dir reachable from two scopes counts once

    def add(scope, origin, path):
        rp = os.path.realpath(path)
        if rp in seen:
            return
        seen.add(rp)
        found.append((scope, origin, path))

    personal = os.path.join(home, ".claude", "skills")
    for p in _skill_files(personal):
        add("personal", "", p)

    proj = os.path.join(project_dir, ".claude", "skills")
    proj_origin = os.path.basename(os.path.abspath(project_dir))
    for p in _skill_files(proj):
        add("project", proj_origin, p)

    plugins_root = os.path.join(home, ".claude", "plugins", "marketplaces")
    seen_plugin = set()
    for p in _skill_files(plugins_root):
        norm = p.replace(os.sep, "/")
        if any(frag in norm for frag in PLUGIN_SKIP):
            continue
        skill_name = os.path.basename(os.path.dirname(p))
        rel = os.path.relpath(p, plugins_root)
        plugin_name = rel.split(os.sep)[0]
        key = (plugin_name, skill_name)
        if key in seen_plugin:  # dedupe repeated marketplace copies
            continue
        seen_plugin.add(key)
        add("plugin", plugin_name, p)
    return found


def _skill_files(root):
    if not os.path.isdir(root):
        return []
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if "SKILL.md" in files:
            out.append(os.path.join(dirpath, "SKILL.md"))
    return sorted(out)


# --- analysis -----------------------------------------------------------------

def tokenize(text):
    return {w for w in re.findall(r"[a-z][a-z-]{2,}", text.lower())
            if w not in STOPWORDS}


def extract_triggers(desc):
    """Pull likely trigger phrases out of a description."""
    phrases = re.findall(r"[\"'“‘]([^\"'”’]{2,60})"
                         r"[\"'”’]", desc)
    for verb in ("use when", "trigger on", "triggers on", "use this when"):
        for m in re.finditer(verb + r"\s+(.{5,90}?)(?:[.;]|$)", desc, re.I):
            phrases.append(m.group(1).strip())
    seen, out = set(), []
    for p in phrases:
        p = p.strip(" .,:")
        low = p.lower()
        if p and low not in seen:
            seen.add(low)
            out.append(p)
    return out[:6]


def extract_non_triggers(desc):
    out = []
    for m in re.finditer(r"(?:do not use|don't use|not for|skip)\s*(?:for|when)?"
                         r"\s*(.{5,90}?)(?:[.;]|$)", desc, re.I):
        out.append(m.group(1).strip(" .,:"))
    return out[:3]


def analyse(skill):
    """Attach warnings + a plain-language note to one skill dict."""
    warnings = []
    desc = skill["description"]
    when = skill["when_to_use"]
    # when_to_use is appended to the description in the real skill listing,
    # so treat the two as one block for every analysis below.
    trigger_text = (desc + "\n" + when).strip()
    desc_len = len(desc) + len(when)

    if not desc:
        warnings.append(("high", "No description — Claude cannot know when to "
                                 "trigger this skill."))
    if skill["body_lines"] > BODY_LINE_LIMIT:
        warnings.append(("warn", f"Body is {skill['body_lines']} lines, over "
                                 f"the {BODY_LINE_LIMIT}-line guideline — it "
                                 f"spends extra context every time it loads."))
    if desc_len > DESC_CHAR_CAP:
        warnings.append(("warn", f"Description is {desc_len} chars, over the "
                                 f"{DESC_CHAR_CAP}-char cap — the tail gets "
                                 f"truncated and may lose trigger keywords."))
    low = trigger_text.lower()
    hit = [w for w in AGGRESSIVE_WORDS
           if re.search(r"\b" + re.escape(w) + r"\b", low)]
    if hit:
        warnings.append(("warn", "Broad trigger wording (" + ", ".join(hit)
                         + ") — expect this to fire on many unrelated prompts."))

    skill["trigger_text"] = trigger_text
    skill["triggers"] = extract_triggers(trigger_text)
    skill["non_triggers"] = extract_non_triggers(trigger_text)
    skill["desc_len"] = desc_len
    skill["warnings"] = warnings

    if skill["disable_model_invocation"]:
        invoke = "Manual only — you run /" + skill["name"] + "; Claude never auto-fires it."
    elif not skill["user_invocable"]:
        invoke = "Model only — Claude triggers it; hidden from the / menu."
    else:
        invoke = "Both you and Claude can invoke it."
    skill["invocation"] = invoke

    if any(s == "high" for s, _ in warnings):
        skill["note"] = "Needs attention — see warnings."
    elif any(s == "warn" for s, _ in warnings):
        skill["note"] = "Usable, but watch the flagged trade-off."
    else:
        skill["note"] = "Well-scoped — clear trigger, reasonable size."
    return skill


def find_collisions(skills):
    """Return description-overlap pairs above the Jaccard threshold."""
    toks = [(s["name"], tokenize(s.get("trigger_text", s["description"])))
            for s in skills]
    pairs = []
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            a, b = toks[i][1], toks[j][1]
            if not a or not b:
                continue
            jac = len(a & b) / len(a | b)
            if jac >= COLLISION_THRESHOLD:
                pairs.append({
                    "a": toks[i][0], "b": toks[j][0],
                    "score": round(jac, 2),
                    "shared": sorted(a & b),
                })
    return sorted(pairs, key=lambda p: -p["score"])


def build_actions(skills, collisions):
    """Turn raw findings into a ranked to-do list and a one-line verdict."""
    actions = []  # (rank, severity, text); rank 0 = high, 1 = medium

    for s in skills:
        if not s["description"]:
            actions.append((0, "high", f"{s['name']} — add a description; "
                            "without one Claude cannot trigger it."))
    for c in collisions:
        pct = int(round(c["score"] * 100))
        if c["score"] >= 0.6:
            actions.append((0, "high", f"Merge or cut — {c['a']} and {c['b']} "
                            f"({pct}% description overlap)."))
        else:
            actions.append((1, "medium", f"Disambiguate — {c['a']} and "
                            f"{c['b']} ({pct}% overlap); tighten one "
                            f"description."))
    for s in skills:
        if s["body_lines"] > BODY_LINE_LIMIT:
            actions.append((1, "medium", f"Trim — {s['name']} body is "
                            f"{s['body_lines']} lines, over {BODY_LINE_LIMIT}."))
    for s in skills:
        if any("Broad trigger" in w[1] for w in s["warnings"]):
            actions.append((1, "medium", f"Tighten — {s['name']} uses broad "
                            "trigger wording; it will over-fire."))
    for s in skills:
        if s["desc_len"] > DESC_CHAR_CAP:
            actions.append((1, "medium", f"Shorten — {s['name']} description "
                            f"is {s['desc_len']} chars, over the "
                            f"{DESC_CHAR_CAP}-char cap."))
    actions.sort(key=lambda a: a[0])

    highs = sum(1 for a in actions if a[1] == "high")
    meds = sum(1 for a in actions if a[1] == "medium")
    if highs:
        verdict = f"needs attention — {highs} high, {meds} medium"
    elif meds:
        verdict = f"minor cleanup — {meds} item{'s' if meds != 1 else ''}"
    else:
        verdict = "healthy — no action needed"
    return actions, verdict


# --- HTML rendering -----------------------------------------------------------

CSS = """
:root { --bg:#0e0e11; --card:#16161a; --card2:#1c1c22; --ink:#e8e8ea;
        --mut:#8a8a92; --line:#26262c; --accent:#5b8cff;
        --warn:#e0a64d; --high:#e5736b; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       -webkit-font-smoothing:antialiased; }
.wrap { max-width:1060px; margin:0 auto; padding:36px 24px 90px; }
.brand { display:flex; align-items:baseline; gap:10px; margin-bottom:22px;
         padding-bottom:14px; border-bottom:1px solid var(--line); }
.brand .logo { font-weight:700; letter-spacing:-.01em; }
.brand .ver { color:var(--mut); font-size:12px;
              font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
h1 { font-size:26px; font-weight:650; letter-spacing:-.02em; margin:0 0 4px; }
.sub { color:var(--mut); margin:0 0 30px; font-size:13px; }
.stats { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:24px; }
.stat { background:var(--card); border:1px solid var(--line);
        border-radius:12px; padding:15px 18px; min-width:118px; }
.stat .n { font-size:24px; font-weight:650;
           font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.stat .l { color:var(--mut); font-size:11px; text-transform:uppercase;
           letter-spacing:.06em; margin-top:2px; }
.bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden;
       margin-top:8px; }
.bar > i { display:block; height:100%; background:var(--accent); }
.controls { display:flex; gap:10px; margin-bottom:18px; flex-wrap:wrap; }
input[type=search],select { font:inherit; padding:9px 13px; border-radius:9px;
       border:1px solid var(--line); background:var(--card); color:var(--ink); }
input[type=search] { flex:1; min-width:200px; }
input[type=search]::placeholder { color:var(--mut); }
input:focus,select:focus { outline:none; border-color:var(--accent); }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:.07em;
     color:var(--mut); margin:36px 0 12px; font-weight:600; }
.card { background:var(--card); border:1px solid var(--line);
        border-radius:12px; padding:16px 18px; margin-bottom:9px; }
.card .top { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.card .name { font-weight:600; font-size:15px; }
.scope { font-size:11px; color:var(--mut); border:1px solid var(--line);
         border-radius:6px; padding:1px 7px; }
.desc { color:var(--ink); margin:9px 0 0; font-size:13px; }
.meta { color:var(--mut); font-size:12px; margin-top:8px; }
.note { font-size:12px; margin-top:8px; color:var(--mut); }
.trg { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px;
       background:var(--card2); border:1px solid var(--line); border-radius:6px;
       padding:2px 7px; margin:2px 4px 2px 0; display:inline-block; }
.pill { font-size:10px; border-radius:5px; padding:2px 7px; font-weight:700;
        text-transform:uppercase; letter-spacing:.04em; }
.pill.warn { background:rgba(224,166,77,.16); color:var(--warn); }
.pill.high { background:rgba(229,115,107,.16); color:var(--high); }
.pill.ok { background:rgba(91,140,255,.16); color:var(--accent); }
.w { font-size:12px; margin-top:6px; }
.w.warn { color:var(--warn); } .w.high { color:var(--high); }
.coll { font-size:13px; }
.coll code { background:var(--card2); padding:2px 6px; border-radius:5px;
             font-size:12px; }
.actions { background:var(--card2); }
.actions .verdict { font-weight:650; font-size:15px; margin-bottom:11px; }
.actions ol { margin:0; padding-left:22px; }
.actions li { margin:7px 0; font-size:13px; }
.actions li .pill { margin-right:7px; }
footer { color:var(--mut); font-size:12px; margin-top:54px;
         border-top:1px solid var(--line); padding-top:18px; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
"""

JS = """
const q=document.getElementById('q'), sc=document.getElementById('sc');
function flt(){
  const t=q.value.toLowerCase(), s=sc.value;
  document.querySelectorAll('.card').forEach(c=>{
    const okT=c.dataset.search.includes(t);
    const okS=(s==='all'||c.dataset.scope===s);
    c.style.display=(okT&&okS)?'':'none';
  });
}
q.addEventListener('input',flt); sc.addEventListener('change',flt);
"""


def render_html(skills, collisions, budget, actions=None, verdict=""):
    e = html.escape
    actions = actions or []
    by_scope = {"personal": [], "project": [], "plugin": []}
    for s in skills:
        by_scope.setdefault(s["scope"], []).append(s)

    oversized = sum(1 for s in skills if s["body_lines"] > BODY_LINE_LIMIT)
    aggressive = sum(1 for s in skills
                     if any("Broad trigger" in w[1] for w in s["warnings"]))

    parts = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
             "<meta name=viewport content='width=device-width,initial-scale=1'>",
             "<title>skillscope report</title><style>", CSS, "</style></head>",
             "<body><div class=wrap>",
             f"<div class=brand><span class=logo>skillscope</span>"
             f"<span class=ver>v{VERSION}</span></div>",
             "<h1>Claude Code skill overview</h1>",
             f"<p class=sub>{len(skills)} skills &middot; generated "
             f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
             f"by <a href='https://github.com/Troels-en/skillscope'>"
             f"skillscope</a></p>"]

    pct_real = round(budget["used_pct"])
    pct_bar = min(100, pct_real)
    over = pct_real > 100
    parts.append("<div class=stats>")
    for n, l in [(len(by_scope["personal"]), "personal"),
                 (len(by_scope["project"]), "project"),
                 (len(by_scope["plugin"]), "plugin"),
                 (oversized, "oversized body"),
                 (aggressive, "broad triggers"),
                 (len(collisions), "collision pairs")]:
        parts.append(f"<div class=stat><div class=n>{n}</div>"
                     f"<div class=l>{e(l)}</div></div>")
    n_style = " style='color:var(--high)'" if over else ""
    bar_style = ";background:var(--high)" if over else ""
    parts.append(f"<div class=stat style='flex:1;min-width:220px'>"
                 f"<div class=n{n_style}>{pct_real}%</div>"
                 f"<div class=l>est. description budget used</div>"
                 f"<div class=bar><i style='width:{pct_bar}%{bar_style}'></i>"
                 f"</div></div>")
    parts.append("</div>")

    parts.append("<h2>Recommended actions</h2>")
    parts.append(f"<div class='card actions'><div class=verdict>{e(verdict)}"
                 "</div>")
    if actions:
        parts.append("<ol>")
        for _rank, sev, text in actions:
            parts.append(f"<li><span class='pill {sev}'>{sev}</span>"
                         f"{e(text)}</li>")
        parts.append("</ol>")
    parts.append("</div>")

    parts.append("<div class=controls>"
                 "<input type=search id=q placeholder='Filter skills…'>"
                 "<select id=sc><option value=all>all scopes</option>"
                 "<option value=personal>personal</option>"
                 "<option value=project>project</option>"
                 "<option value=plugin>plugin</option></select></div>")

    if collisions:
        parts.append("<h2>Trigger collisions</h2>")
        for c in collisions:
            parts.append(f"<div class='card coll'>"
                         f"<code>{e(c['a'])}</code> &harr; "
                         f"<code>{e(c['b'])}</code> "
                         f"<span class='pill warn'>{c['score']}</span>"
                         f"<div class=meta>shared trigger words: "
                         f"{e(', '.join(c['shared']))}</div>"
                         f"<div class=note>Two skills described in similar "
                         f"words may fire for the same prompt. Tighten one "
                         f"description or merge them.</div></div>")

    for scope in ("personal", "project", "plugin"):
        group = by_scope.get(scope) or []
        if not group:
            continue
        parts.append(f"<h2>{scope} skills ({len(group)})</h2>")
        for s in sorted(group, key=lambda x: x["name"]):
            parts.append(_render_card(s, e))

    parts.append(
        "<footer>skillscope reads SKILL.md frontmatter only — it never sends "
        "your data anywhere. Budget figure is an estimate (description chars "
        "&divide; 4 vs 1% of the context window). Plugin scope is de-duplicated "
        "by name; cache copies are skipped.</footer>")
    parts.append("</div><script>", )
    parts.append(JS)
    parts.append("</script></body></html>")
    return "".join(parts)


def _render_card(s, e):
    sev = "high" if any(w[0] == "high" for w in s["warnings"]) else (
          "warn" if s["warnings"] else "ok")
    label = {"high": "needs attention", "warn": "review",
             "ok": "healthy"}[sev]
    search = (s["name"] + " " + s.get("trigger_text", s["description"])).lower()
    out = [f"<div class=card data-scope=\"{e(s['scope'])}\" "
           f"data-search=\"{e(search)}\">"]
    out.append("<div class=top>"
               f"<span class=name>{e(s['name'])}</span>"
               f"<span class=scope>{e(s['scope'])}"
               + (f": {e(s['origin'])}" if s.get("origin") else "")
               + "</span>"
               f"<span class='pill {sev}'>{label}</span></div>")
    desc = s["description"] or "(no description)"
    if len(desc) > 320:
        desc = desc[:320] + "…"
    out.append(f"<div class=desc>{e(desc)}</div>")
    if s["triggers"]:
        out.append("<div class=meta>triggers: " + "".join(
            f"<span class=trg>{e(t)}</span>" for t in s["triggers"]) + "</div>")
    if s["non_triggers"]:
        out.append("<div class=meta>not for: "
                   + e("; ".join(s["non_triggers"])) + "</div>")
    out.append(f"<div class=meta>{s['body_lines']} body lines &middot; "
               f"{e(s['invocation'])}</div>")
    for level, msg in s["warnings"]:
        out.append(f"<div class='w {level}'>! {e(msg)}</div>")
    out.append(f"<div class=note>{e(s['note'])}</div>")
    out.append("</div>")
    return "".join(out)


# --- main ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Overview of your Claude Code skills as an HTML report.")
    ap.add_argument("--project", default=os.getcwd(),
                    help="project dir to scan for .claude/skills (default: cwd)")
    ap.add_argument("--out", default="skill-report.html",
                    help="HTML output path (default: skill-report.html)")
    ap.add_argument("--json", default="",
                    help="also write machine-readable JSON to this path")
    ap.add_argument("--context-window", type=int, default=200000,
                    help="model context window in tokens, for the budget "
                         "estimate (default: 200000)")
    ap.add_argument("--open", action="store_true",
                    help="open the report in your browser when done")
    args = ap.parse_args()
    if args.context_window <= 0:
        ap.error("--context-window must be a positive number of tokens")

    located = discover(args.project)
    if not located:
        print("No SKILL.md files found under ~/.claude or the project dir.",
              file=sys.stderr)
        return 1

    skills = []
    for scope, origin, path in located:
        parsed = parse_skill(path)
        if not parsed:
            continue
        parsed["scope"] = scope
        parsed["origin"] = origin
        skills.append(analyse(parsed))

    collisions = find_collisions(skills)
    actions, verdict = build_actions(skills, collisions)

    # Budget: description text always sits in context for model-invocable
    # skills. Estimate tokens as chars/4; budget is 1% of the context window.
    # Each skill contributes at most DESC_CHAR_CAP to the listing — anything
    # past the cap is truncated and never reaches context.
    desc_chars = sum(min(s["desc_len"], DESC_CHAR_CAP) for s in skills
                     if not s["disable_model_invocation"])
    est_tokens = desc_chars / 4
    budget_tokens = args.context_window * 0.01
    budget = {
        "desc_chars": desc_chars,
        "est_tokens": round(est_tokens),
        "budget_tokens": round(budget_tokens),
        "used_pct": (est_tokens / budget_tokens * 100) if budget_tokens else 0,
    }

    report = render_html(skills, collisions, budget, actions, verdict)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Wrote {args.out}  ({len(skills)} skills, "
          f"{len(collisions)} collision pairs)")

    print(f"\nVerdict: {verdict}")
    for _rank, sev, text in actions[:5]:
        print(f"  [{sev:>6}] {text}")
    if len(actions) > 5:
        print(f"  … {len(actions) - 5} more in the report")

    if args.json:
        payload = {"generated": datetime.now(timezone.utc).isoformat(),
                   "verdict": verdict,
                   "actions": [{"severity": sev, "text": text}
                               for _r, sev, text in actions],
                   "budget": budget, "collisions": collisions,
                   "skills": [{k: v for k, v in s.items() if k != "path"}
                              for s in skills]}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote {args.json}")

    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
