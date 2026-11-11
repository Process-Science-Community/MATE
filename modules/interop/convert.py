"""Process-model conversions between artifact payloads; pm4py does the work.

Every function takes and returns payloads in the shapes the artifact types fix
(`mate.sdk.artifacts`: ``process_tree``, ``petri_net``, ``bpmn``), the same shapes
Discovery emits, so an output feeds any module that consumes that type. Blocking and
CPU-bound: call them off the event loop. pm4py and lxml are imported lazily.
"""

from __future__ import annotations

from typing import Any

# The petri_net serialiser is a copy of discovery's `serialize_petri_net` (a module
# cannot import another module's code); keep the two shapes in step.


def _petri_payload(net: Any, im: Any, fm: Any) -> dict[str, Any]:
    place_id = {p: f"p{i}" for i, p in enumerate(net.places)}
    transition_id = {t: f"t{i}" for i, t in enumerate(net.transitions)}
    places = [
        {
            "id": place_id[p],
            "label": str(p.name),
            "is_initial": p in im,
            "is_final": p in fm,
            "tokens": int(im[p]) if p in im else 0,
        }
        for p in net.places
    ]
    transitions = [
        {
            "id": transition_id[t],
            "label": "" if t.label is None else str(t.label),
            "is_invisible": t.label is None,
            "name": str(t.name),
        }
        for t in net.transitions
    ]
    arcs: list[dict[str, Any]] = []
    for arc in net.arcs:
        src = place_id.get(arc.source) or transition_id.get(arc.source)
        tgt = place_id.get(arc.target) or transition_id.get(arc.target)
        if src is not None and tgt is not None:
            weight = int(getattr(arc, "weight", 1) or 1)
            arcs.append({"id": f"{src}__{tgt}", "source": src, "target": tgt, "weight": weight})
    return {"kind": "petri_net", "places": places, "transitions": transitions, "arcs": arcs}


def _petri_net(payload: dict[str, Any]) -> tuple[Any, Any, Any]:
    from pm4py.objects.petri_net.obj import Marking, PetriNet
    from pm4py.objects.petri_net.utils.petri_utils import add_arc_from_to

    net = PetriNet("interop")
    im, fm = Marking(), Marking()
    nodes: dict[str, Any] = {}
    for p in payload["places"]:
        place = nodes[p["id"]] = PetriNet.Place(p["id"])
        net.places.add(place)
        if p.get("is_initial"):
            im[place] = int(p.get("tokens") or 1)
        if p.get("is_final"):
            fm[place] = 1
    for t in payload["transitions"]:
        # An empty label is an invisible (tau) transition.
        transition = nodes[t["id"]] = PetriNet.Transition(t["id"], t["label"] or None)
        net.transitions.add(transition)
    for a in payload["arcs"]:
        add_arc_from_to(nodes[a["source"]], nodes[a["target"]], net, int(a.get("weight") or 1))
    return net, im, fm


def _bpmn_payload(graph: Any) -> dict[str, Any]:
    # Same exporter and output as discovery's `serialize_bpmn` (pm4py.write_bpmn
    # without auto layout), minus the temp file.
    from pm4py.objects.bpmn.exporter.variants.etree import get_xml_string

    return {"kind": "bpmn", "version": 1, "xml": get_xml_string(graph).decode("utf-8")}


def _process_tree(node: dict[str, Any], parent: Any = None) -> Any:
    from pm4py.objects.process_tree.obj import Operator, ProcessTree

    operator = node.get("operator")
    tree = ProcessTree(
        operator=Operator[operator.upper()] if operator else None,
        parent=parent,
        label=node.get("label"),
    )
    tree.children = [_process_tree(child, tree) for child in node.get("children") or []]
    return tree


def tree_to_petri(payload: dict[str, Any]) -> dict[str, Any]:
    import pm4py

    return _petri_payload(*pm4py.convert_to_petri_net(_process_tree(payload["root"])))


def petri_to_bpmn(payload: dict[str, Any]) -> dict[str, Any]:
    import pm4py

    return _bpmn_payload(pm4py.convert_to_bpmn(*_petri_net(payload)))


def _bpmn_graph(xml: bytes) -> Any:
    from pm4py.objects.bpmn.importer.variants.lxml import import_from_string

    return import_from_string(xml)


def bpmn_to_petri(payload: dict[str, Any]) -> dict[str, Any]:
    import pm4py

    return _petri_payload(*pm4py.convert_to_petri_net(_bpmn_graph(payload["xml"].encode())))


def petri_to_pnml(payload: dict[str, Any]) -> bytes:
    from pm4py.objects.petri_net.exporter.variants.pnml import export_petri_as_string

    net, im, fm = _petri_net(payload)
    return export_petri_as_string(net, im, fm)


def check_xml(data: bytes) -> Any:
    """The root element of *data*; refused unless it is well-formed XML without a
    DOCTYPE.

    No DOCTYPE means no entity declarations, so pm4py's (entity-resolving) parser
    cannot be pointed at an external entity or an entity bomb afterwards. The check
    parses with entities, DTD loading and network access off, so it is safe itself
    and sees the document in whatever encoding it declares.
    """
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Not well-formed XML: {exc}") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError("XML with a DOCTYPE declaration is not accepted.")
    return root


def _labelled(petri: dict[str, Any], what: str) -> dict[str, Any]:
    if not any(t["label"] for t in petri["transitions"]):
        raise ValueError(f"The {what} has no labelled activities.")
    return petri


def pnml_to_petri(data: bytes) -> dict[str, Any]:
    from pm4py.objects.petri_net.importer.variants.pnml import import_net_from_string

    # pm4py reads inhibitor and reset arcs, but the petri_net payload holds normal arcs
    # only, so keeping them would silently change what the net allows.
    arc_types = {
        (t.text or "").strip().lower()
        for t in check_xml(data).iterfind(".//{*}arc/{*}arctype/{*}text")
    } - {"", "normal"}
    if arc_types:
        raise ValueError(
            f"The Petri net has {' and '.join(sorted(arc_types))} arcs, which MATE does not "
            "support: only normal arcs can be imported."
        )
    return _labelled(_petri_payload(*import_net_from_string(data)), "Petri net")


def bpmn_from_xml(data: bytes) -> dict[str, Any]:
    """The ``bpmn`` payload of an uploaded BPMN file, re-exported by pm4py so it is
    normalised the way Discovery's is and known to convert to a Petri net."""
    import pm4py

    check_xml(data)
    graph = _bpmn_graph(data)
    _labelled(_petri_payload(*pm4py.convert_to_petri_net(graph)), "BPMN model")
    return _bpmn_payload(graph)
