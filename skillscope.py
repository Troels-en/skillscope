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

# Official guideline: keep a SKILL.md body under 500 lines.
BODY_LINE_LIMIT = 500
# Official cap: description + when_to_use is truncated at 1536 chars in the
# skill listing. Going over means triggers get silently cut.
DESC_CHAR_CAP = 1536
# Words that make a description fire very broadly — usually unintended.
AGGRESSIVE_WORDS = ("any ", "every ", "always", "all conversation",
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
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    front = m.group(1) if m else ""
    body = text[m.end():] if m else text

    fields = _parse_frontmatter(front)
    name = fields.get("name") or os.path.basename(os.path.dirname(path))
    desc = fields.get("description", "").strip()
    when = fields.get("when_to_use", "").strip()
    body_lines = body.count("\n") + 1 if body.strip() else 0

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
        km = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
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

    personal = os.path.join(home, ".claude", "skills")
    for p in _skill_files(personal):
        found.append(("personal", "", p))

    proj = os.path.join(project_dir, ".claude", "skills")
    for p in _skill_files(proj):
        found.append(("project", os.path.basename(os.path.abspath(project_dir)), p))

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
        found.append(("plugin", plugin_name, p))
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
    desc_len = len(desc) + len(skill["when_to_use"])

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
    low = desc.lower()
    hit = [w.strip() for w in AGGRESSIVE_WORDS if w in low]
    if hit:
        warnings.append(("warn", "Broad trigger wording (" + ", ".join(hit)
                         + ") — expect this to fire on many unrelated prompts."))

    skill["triggers"] = extract_triggers(desc)
    skill["non_triggers"] = extract_non_triggers(desc)
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
    toks = [(s["name"], tokenize(s["description"])) for s in skills]
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


# --- HTML rendering -----------------------------------------------------------

CSS = """
:root { --bg:#fbfbfa; --card:#fff; --ink:#1a1a1a; --mut:#6b6b6b;
        --line:#e4e4e2; --accent:#2563eb; --warn:#b45309; --high:#b91c1c; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.wrap { max-width:1080px; margin:0 auto; padding:40px 24px 80px; }
h1 { font-size:24px; margin:0 0 4px; }
.sub { color:var(--mut); margin:0 0 28px; font-size:13px; }
.stats { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:28px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:14px 18px; min-width:120px; }
.stat .n { font-size:22px; font-weight:600; }
.stat .l { color:var(--mut); font-size:12px; text-transform:uppercase;
           letter-spacing:.04em; }
.bar { height:8px; background:var(--line); border-radius:4px; overflow:hidden;
       margin-top:6px; }
.bar > i { display:block; height:100%; background:var(--accent); }
.controls { display:flex; gap:10px; margin-bottom:18px; flex-wrap:wrap; }
input[type=search],select { font:inherit; padding:8px 12px; border-radius:8px;
       border:1px solid var(--line); background:var(--card); }
input[type=search] { flex:1; min-width:200px; }
h2 { font-size:14px; text-transform:uppercase; letter-spacing:.05em;
     color:var(--mut); margin:34px 0 12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:16px 18px; margin-bottom:10px; }
.card .top { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.card .name { font-weight:600; font-size:15px; }
.scope { font-size:11px; color:var(--mut); border:1px solid var(--line);
         border-radius:5px; padding:1px 6px; }
.desc { color:var(--ink); margin:8px 0 0; font-size:13px; }
.meta { color:var(--mut); font-size:12px; margin-top:8px; }
.note { font-size:12px; margin-top:8px; color:var(--mut); }
.trg { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px;
       background:var(--bg); border:1px solid var(--line); border-radius:5px;
       padding:1px 6px; margin:2px 4px 2px 0; display:inline-block; }
.pill { font-size:11px; border-radius:5px; padding:1px 7px; font-weight:600; }
.pill.warn { background:#fef3c7; color:var(--warn); }
.pill.high { background:#fee2e2; color:var(--high); }
.pill.ok { background:#e7f0ff; color:var(--accent); }
.w { font-size:12px; margin-top:6px; }
.w.warn { color:var(--warn); } .w.high { color:var(--high); }
.coll { font-size:13px; }
.coll code { background:var(--bg); padding:1px 5px; border-radius:4px; }
footer { color:var(--mut); font-size:12px; margin-top:50px;
         border-top:1px solid var(--line); padding-top:16px; }
a { color:var(--accent); }
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


def render_html(skills, collisions, budget):
    e = html.escape
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
    search = (s["name"] + " " + s["description"]).lower()
    out = [f"<div class=card data-scope={e(s['scope'])} "
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

    # Budget: description text always sits in context for model-invocable
    # skills. Estimate tokens as chars/4; budget is 1% of the context window.
    desc_chars = sum(s["desc_len"] for s in skills
                     if not s["disable_model_invocation"])
    est_tokens = desc_chars / 4
    budget_tokens = args.context_window * 0.01
    budget = {
        "desc_chars": desc_chars,
        "est_tokens": round(est_tokens),
        "budget_tokens": round(budget_tokens),
        "used_pct": (est_tokens / budget_tokens * 100) if budget_tokens else 0,
    }

    report = render_html(skills, collisions, budget)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Wrote {args.out}  ({len(skills)} skills, "
          f"{len(collisions)} collision pairs)")

    if args.json:
        payload = {"generated": datetime.now(timezone.utc).isoformat(),
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
