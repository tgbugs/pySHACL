# -*- coding: utf-8 -*-
#
from os import path
from io import IOBase, BytesIO
import platform
from urllib import request
from urllib.error import HTTPError

import rdflib
try:
    import rdflib_jsonld
    has_json_ld = True
except IndexError:
    has_json_ld = False

is_windows = platform.system() == "Windows"


def get_rdf_from_web(url):
    headers = {'Accept':
               'text/turtle, application/rdf+xml, '
               'application/ld+json, application/n-triples,'
               'text/plain'}
    r = request.Request(url, headers=headers)
    resp = request.urlopen(r)
    code = resp.getcode()
    if not (200 <= code <= 210):
        raise RuntimeError("Cannot pull RDF URL from the web: {}, code: {}"
                           .format(url, str(code)))
    known_format = None
    content_type = resp.headers.get('Content-Type', None)
    if content_type:
        if content_type.startswith("text/turtle"):
            known_format = "turtle"
        elif content_type.startswith("application/rdf+xml"):
            known_format = "xml"
        elif content_type.startswith("application/xml"):
            known_format = "xml"
        elif content_type.startswith("application/ld+json"):
            known_format = "json-ld"
        elif content_type.startswith("application/n-triples"):
            known_format = "nt"
    return resp, known_format


def load_from_source(source, g=None, rdf_format=None, do_owl_imports=False, import_chain=None):
    source_is_graph = False
    source_is_open = False
    source_was_open = False
    source_is_file = False
    source_is_bytes = False
    filename = None
    public_id = None
    uri_prefix = None
    is_imported_graph = do_owl_imports and isinstance(do_owl_imports, int) and \
                        do_owl_imports > 1
    if isinstance(source, rdflib.Graph):
        source_is_graph = True
        if g is None:
            g = source
        else:
            raise RuntimeError("Cannot pass in both target=rdflib.Graph and g=graph.")
    elif isinstance(source, IOBase) and hasattr(source, 'read'):
        source_is_file = True
        if hasattr(source, 'closed'):
            source_is_open = not bool(source.closed)
            source_was_open = source_is_open
        else:
            # Assume it is open now and it was open when we started.
            source_is_open = True
            source_was_open = True
        filename = source.name
        if is_windows:
            public_id = "file:///{}#".format(path.abspath(filename).replace("\\", "/"))
        else:
            public_id = "file://{}#".format(path.abspath(filename))
    elif isinstance(source, str):
        if is_windows and source.startswith('file:///'):
            public_id = source
            source_is_file = True
            filename = source[8:]
        elif source.startswith('file://'):
            public_id = source
            source_is_file = True
            filename = source[7:]
        elif source.startswith('http:') or source.startswith('https:'):
            public_id = source
            try:
                source, rdf_format = get_rdf_from_web(source)
            except HTTPError:
                if is_imported_graph:
                    return g
                else:
                    raise
            source_is_open = True
            filename = source.geturl()
        else:
            first_char = source[0]
            if is_windows and (first_char == '\\' or
               (len(source) > 3 and source[1:3] == ":\\")):
                source_is_file = True
                filename = source
            elif first_char == '/' or source[0:3] == "./":
                source_is_file = True
                filename = source
            elif first_char == '#' or first_char == '@' \
                or first_char == '<' or first_char == '\n' \
                    or first_char == '{' or first_char == '[':
                # Contains some JSON or XML or Turtle stuff
                source_is_file = False
            elif len(source) < 140:
                source_is_file = True
                filename = source
        if public_id and not public_id.endswith('#'):
            public_id = "{}#".format(public_id)
        if not source_is_file and not source_is_open:
            source = source.encode('utf-8')
            source_is_bytes = True
    elif isinstance(source, bytes):
        if source.startswith(b'file:///') or\
           source.startswith(b'file://') or\
           source.startswith(b'http:') or source.startswith(b'https:'):
            raise ValueError("file:// and http:// strings should be given as str, not bytes.")
        first_char = source[0:1]
        if first_char == b'#' or first_char == b'@' \
            or first_char == b'<' or first_char == b'\n' \
                or first_char == b'{' or first_char == b'[':
            # Contains some JSON or XML or Turtle stuff
            source_is_file = False
        elif len(source) < 140:
            source_is_file = True
            filename = source.decode('utf-8')
        if not source_is_file:
            source_is_bytes = True
    else:
        raise ValueError("Cannot determine the format of the input graph")
    if g is None:
        g = rdflib.Graph()
    else:
        if not isinstance(g, rdflib.Graph):
            raise RuntimeError("Passing in g must be a Graph.")
    if filename:
        if filename.endswith('.ttl'):
            rdf_format = rdf_format or 'turtle'
        elif filename.endswith('.nt'):
            rdf_format = rdf_format or 'nt'
        elif filename.endswith('.n3'):
            rdf_format = rdf_format or 'n3'
        elif filename.endswith('.json'):
            rdf_format = rdf_format or 'json-ld'
        elif filename.endswith('.xml') or filename.endswith('.rdf'):
            rdf_format = rdf_format or 'xml'
    if source_is_file and filename and not source_is_open:
        filename = path.abspath(filename)
        if not public_id:
            if is_windows:
                public_id = "file:///{}#".format(filename.replace('\\', '/'))
            else:
                public_id = "file://{}#".format(filename)
        source = open(filename, mode='rb')
        source_is_open = True
    if source_is_open:
        data = source.read()
        # If the target was open to begin with, leave it open.
        if not source_was_open:
            source.close()
        elif hasattr(source, 'seek'):
            try:
                source.seek(0)
            except Exception:
                pass
        source = data
        source_is_bytes = True

    if source_is_bytes:
        source = BytesIO(source)
        if (rdf_format == "json-ld" or rdf_format == "json") and not has_json_ld:
            raise RuntimeError(
                "Cannot load a JSON-LD file if rdflib_jsonld is not installed.")
        if rdf_format == 'turtle' or rdf_format == 'n3':
            # SHACL Shapes files and Data files can have extra RDF Metadata in the
            # Top header block, including #BaseURI and #Prefix.
            while True:
                try:
                    l = source.readline()
                    assert l is not None and len(l) > 0
                except AssertionError:
                    break
                # Strip line from start
                while len(l) > 0 and l[0:1] in b' \t\n\r\x0B\x0C\x85\xA0':
                    l = l[1:]
                # We reached the end of the line, check the next line
                if len(l) < 1:
                    continue
                # If this is not a comment, then this is the first non-comment line, we're done.
                if not l[0:1] == b'#':
                    break
                # Strip from start again, but now removing hashes too.
                while len(l) > 0 and l[0:1] in b'# \t\xA0':
                    l = l[1:]
                # Strip line from end
                while len(l) > 0 and l[-1:] in b' \t\n\r\x0B\x0C\x85\xA0':
                    l = l[:-1]
                spl = l.split(b':', 1)
                if len(spl) < 2:
                    continue
                keyword = spl[0].lower()
                # Strip keyword end
                while len(keyword) > 0 and keyword[-1:] in b' \t\n\r\x0B\x0C\x85\xA0':
                    keyword = keyword[:-1]
                if len(keyword) < 1:
                    continue
                wordval = spl[1]
                # Strip wordval start
                while len(wordval) > 0 and wordval[0:1] in b' \t\n\r\x0B\x0C\x85\xA0':
                    wordval = wordval[1:]
                if len(wordval) < 1:
                    continue
                wordval = wordval.decode('utf-8')
                if keyword == b"baseuri":
                    public_id = wordval
                elif keyword == b"prefix":
                    uri_prefix = wordval
            source.seek(0)
        g.parse(source=source, format=rdf_format, publicID=public_id)
        source_is_graph = True

    if not source_is_graph:
        raise RuntimeError("Error opening graph from source.")

    if public_id:
        if uri_prefix:
            if is_imported_graph and uri_prefix == '':
                # Don't reassign blank prefix, when importing subgraph
                pass
            else:
                has_named_prefix = g.store.namespace(uri_prefix)
                if not has_named_prefix:
                    g.namespace_manager.bind(uri_prefix, public_id)
        elif not is_imported_graph:
            existing_blank_prefix = g.store.namespace('')
            if not existing_blank_prefix:
                g.namespace_manager.bind('', public_id)
    if do_owl_imports:
        if isinstance(do_owl_imports, int):
            if do_owl_imports > 3:
                return g
        else:
            do_owl_imports = 1

        if import_chain is None:
            import_chain = []
        if public_id and (public_id.endswith('#') or public_id.endswith('/')):
            root_id = rdflib.URIRef(public_id[:-1])
        else:
            root_id = rdflib.URIRef(public_id) if public_id else None
        done_imports = 0
        if root_id is not None:
            owl_imports = list(g.objects(root_id, rdflib.OWL.imports))
            if len(owl_imports) > 0:
                import_chain.append(root_id)
            for o in owl_imports:
                if o in import_chain:
                    continue
                load_from_source(o, g=g, do_owl_imports=do_owl_imports + 1, import_chain=import_chain)
                done_imports += 1
        if done_imports < 1 and public_id is not None and root_id != public_id:
            public_id_uri = rdflib.URIRef(public_id)
            owl_imports = list(g.objects(public_id_uri, rdflib.OWL.imports))
            if len(owl_imports) > 0:
                import_chain.append(public_id_uri)
            for o in owl_imports:
                if o in import_chain:
                    continue
                load_from_source(o, g=g, do_owl_imports=do_owl_imports + 1, import_chain=import_chain)
                done_imports += 1
        if done_imports < 1:
            ontologies = g.subjects(rdflib.RDF.type, rdflib.OWL.Ontology)
            for ont in ontologies:
                if ont == root_id or ont == public_id:
                    continue
                if ont in import_chain:
                    continue
                owl_imports = list(g.objects(ont, rdflib.OWL.imports))
                if len(owl_imports) > 0:
                    import_chain.append(ont)
                for o in owl_imports:
                    if o in import_chain:
                        continue
                    load_from_source(o, g=g, do_owl_imports=do_owl_imports + 1, import_chain=import_chain)
                    done_imports += 1
    return g
