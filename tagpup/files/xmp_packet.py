"""The properties TagPup owns in an XMP packet, read and edited as text (docs/PLAN_OWN_METADATA.md, M2).

Pure: bytes in, bytes out, no file and no container; a JPEG's segment and a clip's box both hand it the packet alone.

`read(packet)` is {name: value} of the owned properties the packet holds (a Bag a list of str, the rest a str).
`edit(packet, changes)` sets each named property to its value (None, '' or [] clears it) and answers `containers.Edited`: every byte
outside the owned properties' own elements is the packet's as it was -- other namespaces, comments, attribute order,
quotes, whitespace -- and the change is paid for out of the padding before `<?xpacket end`, so the packet keeps its
length; only `grow=True` lets it grow, laying `reserve` bytes of fresh padding. A value equal to what the packet holds
changes no byte; a Bag keeps the order it is given, a repeat dropped.

Either answers `containers.Refusal`, a sentence, for a packet it does not wholly understand: it never repairs one and never returns
bytes on doubt (an edit is parsed again and read back before it is answered). What it refuses, with the counts of the
three libraries behind each rule: docs/domains/files.md, "The XMP packet editor".
"""
import re
from xml.parsers import expat

from tagpup.files import containers

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_DC = "http://purl.org/dc/elements/1.1/"
_XML_LANG = ("http://www.w3.org/XML/1998/namespace", "lang")
#: name: (namespace URI, local name, form). A property is found by its URI; the name's prefix is the one declared for a
#: packet that has no declaration of the URI.
PROPERTIES = {
    "dc:subject": (_DC, "subject", "Bag"),
    "dc:description": (_DC, "description", "Alt"),
    "dc:title": (_DC, "title", "Alt"),
    "lr:hierarchicalSubject": ("http://ns.adobe.com/lightroom/1.0/", "hierarchicalSubject", "Bag"),
    "exif:DateTimeOriginal": ("http://ns.adobe.com/exif/1.0/", "DateTimeOriginal", "Text"),
    "xmp:CreateDate": ("http://ns.adobe.com/xap/1.0/", "CreateDate", "Text"),
    "tiff:Orientation": ("http://ns.adobe.com/tiff/1.0/", "Orientation", "Text"),
    "xmpMM:DocumentID": ("http://ns.adobe.com/xap/1.0/mm/", "DocumentID", "Text"),
    "xmpMM:PreservedFileName": ("http://ns.adobe.com/xap/1.0/mm/", "PreservedFileName", "Text"),
}
_NAMES = {(uri, local): name for name, (uri, local, _) in PROPERTIES.items()}
#: The largest packet read or made: a JPEG's main packet is under it; a caller with a larger one passes `limit`.
LIMIT = 64 * 1024
#: The whitespace a grown packet is given after its XML: ExifTool's, a line break and 24 lines of 100 spaces (3,549 of
#: the 3,558 wrapped packets read).
RESERVE = 1 + 24 * 101
_TAG_END = re.compile(rb"(?:[^>'\"]|'[^']*'|\"[^\"]*\")*>")
_BEGIN = re.compile(rb"<\?xpacket begin=[^>]*\?>\s*\Z")
_END = re.compile(rb"(\s*)<\?xpacket end=(['\"])w\2\?>\Z")
#: What XML 1.0 cannot carry, even as a character reference (its Char production).
_NOT_XML = re.compile("[^\t\n\r -%s%s-%s%s-%s]" % (chr(0xD7FF), chr(0xE000), chr(0xFFFD), chr(0x10000), chr(0x10FFFF)))


class _Refused(Exception):
    pass


class _Node:
    """An element: its namespace URI, local name and prefix, attributes by (URI, local), the {URI: prefix} in scope, and
    its byte offsets: start, after its start tag, its end tag's start, its end (the last two the first's for <a/>)."""
    __slots__ = ("uri", "local", "prefix", "attrs", "scope", "start", "open_end", "close_start", "end", "children",
                 "text")

    def __init__(self, name, attrs, scope, start, open_end):
        parts = name.split(" ")
        self.uri, self.local = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
        self.prefix = parts[2] if len(parts) > 2 else ""
        self.attrs = {tuple(key.split(" ")[:2]): value for key, value in attrs.items()}
        self.scope, self.start, self.open_end = scope, start, open_end
        self.close_start = self.end = open_end
        self.children, self.text = [], []

    def name(self):
        return (self.uri, self.local)


def _tree(data):
    """The root _Node of `data`; raises _Refused for what is not well-formed XML with namespaces, or holds a DTD, a
    comment, a CDATA section, a processing instruction inside it, or text beside an element: an edit that removes a
    property splices from the element before it, so nothing but whitespace may lie between two."""
    parser = expat.ParserCreate("UTF-8", " ")
    parser.namespace_prefixes = True
    stack, pending, roots = [], {}, []

    def doctype(*_):
        raise _Refused("The packet holds a DTD or an entity declaration.")

    def start(name, attrs):
        at = parser.CurrentByteIndex
        scope = dict(stack[-1].scope if stack else {})
        scope.update(pending)
        pending.clear()
        node = _Node(name, attrs, scope, at, _TAG_END.match(data, at).end())
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)

    def end(_name):
        node = stack.pop()
        if node.children and "".join(node.text).strip():
            raise _Refused("The packet holds text beside an element.")
        if data[node.open_end - 2:node.open_end] != b"/>":
            node.close_start = parser.CurrentByteIndex
            node.end = data.index(b">", node.close_start) + 1

    def text(chunk):
        if stack:
            stack[-1].text.append(chunk)

    def other(*_):
        if stack:
            raise _Refused("The packet holds a comment, a CDATA section or a processing instruction.")

    parser.StartDoctypeDeclHandler = parser.EntityDeclHandler = doctype
    parser.CommentHandler = parser.StartCdataSectionHandler = parser.ProcessingInstructionHandler = other
    parser.StartElementHandler, parser.EndElementHandler, parser.CharacterDataHandler = start, end, text
    parser.StartNamespaceDeclHandler = lambda prefix, uri: pending.__setitem__(uri, prefix or "")
    try:
        parser.Parse(data, True)
    except expat.ExpatError as problem:
        raise _Refused("The packet is not well-formed XML (%s)." % expat.ErrorString(problem.code)) from None
    return roots[0]


def _text(node):
    """`node`'s text, refusing one with children or written as <a/>."""
    if node.children or node.end == node.open_end:
        raise _Refused("not text")
    return "".join(node.text)


def _value(name, node):
    """The value of property `name` held in `node`, refusing a form TagPup does not write."""
    form = PROPERTIES[name][2]
    try:
        if node.attrs:
            raise _Refused("attributes")
        if form == "Text":
            return _text(node)
        container = node.children[0] if len(node.children) == 1 else None
        items = container.children if container else []
        if (container is None or container.name() != (RDF, form) or container.attrs or not items
                or "".join(node.text + container.text).strip() or any(item.name() != (RDF, "li") for item in items)):
            raise _Refused("container")
        if form == "Alt":
            if len(items) != 1 or items[0].attrs != {_XML_LANG: "x-default"}:
                raise _Refused("languages")
            # An empty caption is written <rdf:li xml:lang='x-default'/> (308 of the 3,565 packets read).
            return "" if items[0].end == items[0].open_end else _text(items[0])
        if any(item.attrs for item in items):
            raise _Refused("item attributes")
        return [_text(item) for item in items]
    except _Refused:
        raise _Refused("%s is not in the form TagPup writes (%s)." % (
            name, "text" if form == "Text" else "one rdf:%s of rdf:li" % form)) from None


class _Packet:
    """A packet read: its descriptions, the owned properties' (description, node) and values, and its padding's span
    (None for a packet with no wrapper)."""

    def __init__(self, data, limit):
        if len(data) > limit:
            raise _Refused("The packet is %d bytes, over the %d an XMP packet is read at." % (len(data), limit))
        if data[:3] == b"\xef\xbb\xbf" or data[:2] in (b"\xfe\xff", b"\xff\xfe") or b"\0" in data[:4]:
            raise _Refused("The packet starts with a byte order mark or is UTF-16: only plain UTF-8 is read.")
        self.data = data
        root = _tree(data)
        closing = _END.match(data, root.end)
        if _BEGIN.match(data[:root.start]) and closing:
            self.padding = (root.end, root.end + len(closing.group(1)))
        elif root.start == 0 and not data[root.end:].strip():
            self.padding = None
        else:
            raise _Refused("The packet's <?xpacket?> wrapper is not whole, or bytes lie outside it.")
        rdf = [node for node in root.children if node.name() == (RDF, "RDF")]
        if root.name() != ("adobe:ns:meta/", "xmpmeta") or len(rdf) != 1:
            raise _Refused("The packet holds no rdf:RDF inside an x:xmpmeta.")
        self.descriptions = rdf[0].children
        if not self.descriptions or any(node.name() != (RDF, "Description") for node in self.descriptions):
            raise _Refused("The packet's rdf:RDF holds something other than rdf:Description, or nothing.")
        if len({node.attrs[(RDF, "about")] for node in self.descriptions if (RDF, "about") in node.attrs}) > 1:
            raise _Refused("The packet names two rdf:about values.")
        self.nodes, self.values = {}, {}
        for description in self.descriptions:
            attribute = next((key for key in description.attrs if key in _NAMES), None)
            if attribute:
                raise _Refused("%s is written as an attribute of rdf:Description." % _NAMES[attribute])
            for node in description.children:
                name = _NAMES.get(node.name())
                if name in self.nodes:
                    raise _Refused("%s is written twice." % name)
                if name:
                    self.nodes[name] = (description, node)
                    self.values[name] = _value(name, node)


def read(packet, limit=LIMIT):
    """{name: value} of the owned properties `packet` (bytes) holds, or a Refusal."""
    try:
        return dict(_Packet(packet, limit).values)
    except _Refused as why:
        return containers.Refusal(str(why))


def _escaped(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#xD;").encode("utf-8")


def _wanted(name, value):
    """The value `name` is to hold, normalised (None: cleared); raises _Refused for one it cannot hold."""
    if PROPERTIES[name][2] == "Bag":
        if value is not None and not (isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)):
            raise _Refused("%s takes a list of text." % name)
        value = list(dict.fromkeys(value or ()))
    elif isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    elif value is not None and not isinstance(value, str):
        raise _Refused("%s takes text." % name)
    if any(_NOT_XML.search(text) for text in (value if isinstance(value, list) else [value or ""])):
        raise _Refused("A value of %s holds a character XML cannot carry (a control character or NUL)." % name)
    return value or None


def _element(qname, rdf, form, value, indent):
    """A new property element as ExifTool lays one out, its lines indented from `indent` (a line break and spaces)."""
    if form == "Text":
        return b"<%s>%s</%s>" % (qname, _escaped(value), qname)
    inner, item, kind = indent + b" ", indent + b"  ", form.encode()
    if form == "Alt":
        items = item + b"<%s:li xml:lang='x-default'>%s</%s:li>" % (rdf, _escaped(value), rdf)
    else:
        items = b"".join(item + b"<%s:li>%s</%s:li>" % (rdf, _escaped(text), rdf) for text in value)
    return b"<%s>%s<%s:%s>%s%s</%s:%s>%s</%s>" % (qname, inner, rdf, kind, items, inner, rdf, kind, indent, qname)


def _changed(packet, name, value):
    """The splice (start, end, bytes) that makes property `name`, which `packet` holds, hold `value`."""
    data, (description, node) = packet.data, packet.nodes[name]
    form = PROPERTIES[name][2]
    if value is None:
        index = description.children.index(node)
        return description.children[index - 1].end if index else description.open_end, node.end, b""
    if form != "Bag":
        held = node.children[0].children[0] if form == "Alt" else node
        if held.end == held.open_end:
            return held.start, held.end, data[held.start:held.end - 2] + b">%s</%s:li>" % (
                _escaped(value), held.prefix.encode())
        return held.open_end, held.close_start, _escaped(value)
    bag = node.children[0]
    items, kept = bag.children, {}
    for item in items:
        kept.setdefault("".join(item.text), data[item.start:item.end])
    gap, rdf = data[bag.open_end:items[0].start], items[0].prefix.encode()
    lis = b"".join(gap + kept.get(text, b"<%s:li>%s</%s:li>" % (rdf, _escaped(text), rdf)) for text in value)
    return bag.open_end, bag.close_start, lis + data[items[-1].end:bag.close_start]


def _splices(packet, changes):
    """[(start, end, bytes)] that make `changes` ({name: normalised value}, in PROPERTIES' order) of `packet`."""
    data, out, declared = packet.data, [], {}
    for name, value in changes.items():
        uri, local, form = PROPERTIES[name]
        if name in packet.nodes:
            out.append(_changed(packet, name, value))
            continue
        if value is None:
            continue
        description = declared.get(uri) or next((d for d in packet.descriptions if uri in d.scope), None)
        if description is None:
            description = declared[uri] = packet.descriptions[0]
            prefix = name.split(":")[0]
            if prefix in description.scope.values():
                raise _Refused("The prefix %s names another namespace in the packet." % prefix)
            tag = data[description.start:description.open_end]
            at = tag.rfind(b"xmlns:")
            gap = tag[len(tag[:at].rstrip()):at] if at > 0 else b" "
            out.append((description.open_end - 1, description.open_end - 1,
                        gap + b"xmlns:%s='%s'" % (prefix.encode(), uri.encode())))
        else:
            prefix = description.scope.get(uri, name.split(":")[0])
        last = description.children[-1].end if description.children else description.open_end
        indent = data[last:description.close_start] + b" "
        qname = (prefix + ":" + local if prefix else local).encode()
        out.append((last, last, indent + _element(qname, description.prefix.encode(), form, value, indent)))
    return out


def padding(size):
    """`size` bytes of whitespace laid as ExifTool lays its padding: lines of 100 spaces, each ended by a line break,
    the first line short. Padding of one size is always the same bytes, so an edit undone gives the packet back."""
    lines = (b"\n" + b" " * 100) * (size // 101 + 1)
    return lines[len(lines) - size + 1:] + b"\n" if size else b""


def _padded(pad, need, grow, reserve):
    """The whitespace `pad` less `need` bytes (more, for a negative need), always ending in a line break; `reserve`
    bytes of it when it holds too few and `grow`."""
    if len(pad) - need >= containers.XMP_PADDING_LEAST:
        return padding(len(pad) - need)
    if grow:
        return padding(reserve)
    raise _Refused("The change needs %d more bytes than the packet's padding holds." % (need - len(pad) + containers.XMP_PADDING_LEAST))


def edit(packet, changes, grow=False, reserve=RESERVE, limit=LIMIT):
    """`packet` (bytes) with `changes` ({name: value}) made: Edited, or a Refusal saying why it was not edited."""
    try:
        unknown = sorted(set(changes) - set(PROPERTIES))
        if unknown:
            raise _Refused("%s is not a property TagPup owns." % unknown[0])
        before = _Packet(packet, limit)
        wanted = {name: _wanted(name, changes[name]) for name in PROPERTIES if name in changes}
        moved = {name: value for name, value in wanted.items() if (before.values.get(name) or None) != value}
        if not moved:
            return containers.Edited(packet, False)
        pieces, at = [], 0
        for start, end, text in sorted(_splices(before, moved), key=lambda splice: splice[:2]):
            pieces += [packet[at:start], text]
            at = end
        if before.padding:
            begin, end = before.padding
            body = b"".join(pieces) + packet[at:begin]
            result = body + _padded(packet[begin:end], len(body) - begin, grow, reserve) + packet[end:]
        else:
            result = b"".join(pieces) + packet[at:]
            if len(result) != len(packet) and not grow:
                raise _Refused("The packet has no wrapper and so no padding: the change needs it to grow.")
        if len(result) > limit:
            raise _Refused("The edited packet would be %d bytes, over %d." % (len(result), limit))
        expected = {name: value for name, value in {**before.values, **moved}.items() if value is not None}
        if _Packet(result, limit).values != expected:
            raise _Refused("The edited packet does not read back as written.")
        return containers.Edited(result, len(result) != len(packet))
    except _Refused as why:
        return containers.Refusal(str(why))
