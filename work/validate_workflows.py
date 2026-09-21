"""Structural validation for the workflows built by register_workflows.py.

Three things are checked, all read-only:

  1. API format -- every node's class_type exists in the running ComfyUI's
     registry, every input key is real, required inputs are present, and every
     link points at an existing node and an in-range output slot.

     This catches the failure mode that actually bites: a misspelt v3 autogrow
     key (e.g. "image_1" instead of "images.image_1") is silently DROPPED by
     the engine, so the workflow runs and just quietly ignores the reference
     image. /object_info is the registry the engine itself uses, and it is the
     only place V3 nodes like TextEncodeQwenImage21 appear at all.

  2. UI format -- each graph is rebuilt in-process and checked structurally:
     every link id referenced by an input exists, and slot indices are in
     range.

  3. UI vs API agreement -- the two must describe the same graph. The
     differences that are intentional (declared in ALLOWED below) are reported
     as such; anything else fails. This is what caught the node-id collision in
     workflow_api_edit_masked.json, where the API ids had to line up with the
     UI ids that Builder assigns in insertion order, notes included.

Run against a live ComfyUI (start it with start_comfyui.bat first):

    python_embeded/python.exe work/validate_workflows.py
"""
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:8199"
WORK = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(WORK)

# --------------------------------------------------------------------------
# Declared, intentional differences between each UI graph and its API file.
# Any difference NOT covered here is a failure.
#
# qwenimage_edit -- the UI and API both permanently wire ten OptionalLoadImage
# nodes. `[no image]` returns None and Qwen's reference encoder skips it, so the
# connected empty slots are genuine fallbacks. The UI has the official
# custom_size switch (EmptyLatentImage + ComfySwitchNode + PrimitiveBoolean);
# the API skips it and feeds TextEncodeQwenImage21's own latent to the sampler,
# which is the same result because ComfySwitchNode defaults to on_false.
#
# qwenimage_edit_masked -- the UI carries an extra LoadImageMask node as the
# alternative mask source. It is deliberately left unlinked (the painted
# LoadImage mask is the default path), so it has no API counterpart.
# --------------------------------------------------------------------------
ALLOWED = {
    "qwenimage_edit": {
        # UI-only extras: the official custom_size switch (EmptyLatentImage +
        # ComfySwitchNode + PrimitiveBoolean). The API file feeds the encoder's
        # latent straight to the sampler, identical to the default on_false path.
        "ui_only_nodes": {"16", "17", "18"},
        "api_only_nodes": set(),
        "ui_only_edge": lambda e: e[2] in {"17", "18"} or e[0] in {"17", "18"},
        "api_only_edge": lambda e: e[3] == "latent_image" and e[0] == "5",
    },
    "qwenimage_edit_masked": {
        "ui_only_nodes": {"6"},          # the alternative LoadImageMask
        "api_only_nodes": set(),
        "ui_only_edge": lambda e: False,
        "api_only_edge": lambda e: False,
    },
}

_objects = {}


def info(class_type):
    """Fetch /object_info for one class, cached. None if the class is unknown."""
    if class_type not in _objects:
        url = f"{BASE}/object_info/{class_type}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                _objects[class_type] = json.load(r).get(class_type)
        except Exception as exc:  # server down, unknown class, ...
            raise SystemExit(f"cannot reach {url}: {exc}") from exc
    return _objects[class_type]


def keys_for(cls):
    """(plain input names, flattened autogrow names) for a node class."""
    plain, grown = set(), set()
    for sect in ("required", "optional"):
        for k, spec in (cls["input"].get(sect) or {}).items():
            plain.add(k)
            if isinstance(spec, list) and spec and spec[0] == "COMFY_AUTOGROW_V3":
                for n in spec[1].get("template", {}).get("names", []):
                    grown.add(f"{k}.{n}")
    return plain, grown


# ---------------------------------------------------------------- API format
def validate_api(path):
    wf = json.load(open(path, encoding="utf-8"))
    errs = []

    # pre-pass so a forward reference does not read as an out-of-range slot
    counts = {}
    for node in wf.values():
        cls = info(node["class_type"])
        if cls is not None:
            counts[node["class_type"]] = len(cls["output"])

    for nid, node in wf.items():
        ct = node["class_type"]
        cls = info(ct)
        if cls is None:
            errs.append(f"[{nid}] unknown class_type {ct!r}")
            continue
        plain, grown = keys_for(cls)
        ins = node.get("inputs", {})

        for k in ins:
            if k not in plain and k not in grown:
                errs.append(f"[{nid}] {ct}: unknown input key {k!r}")

        for k, spec in (cls["input"].get("required") or {}).items():
            # an autogrow with min=0 sits in 'required' but is really optional:
            # supplying no reference images is the text-to-image case
            if (isinstance(spec, list) and spec and spec[0] == "COMFY_AUTOGROW_V3"
                    and spec[1].get("template", {}).get("min", 1) == 0):
                continue
            if k not in ins:
                errs.append(f"[{nid}] {ct}: missing required input {k!r}")

        for k, v in ins.items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                src = wf.get(v[0])
                if src is None:
                    errs.append(f"[{nid}] {ct}.{k} -> missing node {v[0]!r}")
                elif v[1] >= counts.get(src["class_type"], 0):
                    errs.append(f"[{nid}] {ct}.{k} -> {src['class_type']} "
                                f"slot {v[1]} out of range")

    errs += _orphans(
        {n: [v[0] for v in d["inputs"].values()
             if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)]
         for n, d in wf.items()},
        {n: d["class_type"] for n, d in wf.items()})
    return _report(path, len(wf), errs)


def _orphans(adjacency, types):
    """Nodes unreachable from any Save* node."""
    sinks = [n for n, t in types.items() if t.startswith("Save")]
    seen = set()

    def walk(nid):
        if nid in seen or nid not in adjacency:
            return
        seen.add(nid)
        for dst in adjacency[nid]:
            walk(dst)

    for s in sinks:
        walk(s)
    return [f"[{n}] {types[n]}: orphan (unreachable from {len(sinks)} Save node(s))"
            for n in adjacency if n not in seen]


# ----------------------------------------------------------------- UI format
def load_builders():
    src = open(os.path.join(WORK, "register_workflows.py"), encoding="utf-8").read()
    ns = {"__name__": "not_main", "__file__": "register_workflows.py"}
    exec(compile(src, "register_workflows.py", "exec"), ns)
    return ns


def ui_edges(g):
    """(src_id, src_slot, dst_id, dst_input_name) for a UI-format graph."""
    by_link = {l[0]: l for l in g["links"]}
    out, broken = set(), []
    for n in g["nodes"]:
        for inp in n.get("inputs", []):
            lid = inp.get("link")
            if lid is None:
                continue
            if lid not in by_link:
                broken.append(f"node {n['id']} ({n['type']}) input {inp['name']!r} "
                              f"references missing link {lid}")
                continue
            _lid, s, sslot, d, dslot, _t = by_link[lid]
            if dslot != [i for i, x in enumerate(n["inputs"]) if x is inp][0]:
                broken.append(f"node {n['id']} link {lid} target_slot != input index")
            out.add((str(s), sslot, str(d), inp["name"]))
    return out, broken


def validate_ui(name, g):
    """Structural check of a UI graph on its own (no API counterpart needed)."""
    _edges, broken = ui_edges(g)
    ids = {n["id"] for n in g["nodes"]}
    for l in g["links"]:
        if l[1] not in ids or l[3] not in ids:
            broken.append(f"link {l[0]} points at a nonexistent node")
    return _report(f"{name}.ui", len(g["nodes"]), broken)


def crosscheck(name, g, api_path):
    api = json.load(open(api_path, encoding="utf-8"))
    ue, broken = ui_edges(g)
    ae = set()
    for nid, node in api.items():
        for k, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                ae.add((v[0], v[1], nid, k))

    allow = ALLOWED.get(name, {})
    nodes = {n["id"]: n for n in g["nodes"]}
    errs = list(broken)

    ui_ids, api_ids = {str(n["id"]) for n in g["nodes"]}, set(api)
    extra_ui = ui_ids - api_ids - allow.get("ui_only_nodes", set())
    extra_api = api_ids - ui_ids - allow.get("api_only_nodes", set())
    for nid in sorted(extra_ui, key=int):
        if nodes[int(nid)]["type"] != "MarkdownNote":
            errs.append(f"node {nid} ({nodes[int(nid)]['type']}) in UI but not API")
    for nid in sorted(extra_api, key=int):
        errs.append(f"node {nid} ({api[nid]['class_type']}) in API but not UI")
    for nid in sorted(ui_ids & api_ids, key=int):
        u, a = nodes[int(nid)]["type"], api[nid]["class_type"]
        if u != a:
            errs.append(f"node {nid}: UI type {u!r} != API class_type {a!r}")

    uo = allow.get("ui_only_edge", lambda e: False)
    ao = allow.get("api_only_edge", lambda e: False)
    for e in sorted(ue - ae):
        if not uo(e):
            errs.append(f"UI-only edge {e}")
    for e in sorted(ae - ue):
        if not ao(e):
            errs.append(f"API-only edge {e}")

    ok = _report(f"{name}.ui vs {os.path.basename(api_path)}", len(ui_ids), errs)
    print(f"      (edges UI {len(ue)} / API {len(ae)}, "
          f"{len(ue - ae)} UI-only / {len(ae - ue)} API-only, all declared)")
    return ok


def _report(label, n, errs):
    print(f"{label:52s} n={n:3d} -> {'OK' if not errs else 'FAIL'}")
    for e in errs:
        print("     ", e)
    return not errs


def main():
    ns = load_builders()
    ok = True
    for name, path in [
        ("qwenimage", "workflow_api.json"),
        ("qwenimage_edit", "workflow_api_edit.json"),
        ("qwenimage_edit_masked", "workflow_api_edit_masked.json"),
    ]:
        ok &= validate_api(os.path.join(WORK, path))

    for name, build in [
        ("qwenimage", ns["build_qwen"]),
        ("trellis2", ns["build_trellis2"]),
        ("qwenimage_trellis2", ns["build_combined"]),
        ("qwenimage_edit", ns["build_edit"]),
        ("qwenimage_edit_masked", ns["build_edit_masked"]),
    ]:
        ok &= validate_ui(name, build())

    print()
    ok &= crosscheck("qwenimage_edit", ns["build_edit"](),
                     os.path.join(WORK, "workflow_api_edit.json"))
    ok &= crosscheck("qwenimage_edit_masked", ns["build_edit_masked"](),
                     os.path.join(WORK, "workflow_api_edit_masked.json"))
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
