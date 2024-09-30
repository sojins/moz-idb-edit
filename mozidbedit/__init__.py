"""Access Mozilla IndexedDB database contents."""
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# Credits:
#   – Python source code by Erin Yuki Schlarb, 2020–2024.
#   - extended by mirabilos, 2023.

import argparse
import collections.abc
import datetime
import importlib.metadata
import json
import pathlib
import pprint
import re
import os
import shlex
import sys
import typing as ty

import jmespath

from . import mozidb
from . import mozserial

__dir__ = pathlib.Path(__file__).parent
__version__ = importlib.metadata.version("moz-idb-edit")


#HACK: Make `IDBObjectWrapper` be considered a JavaScript Object type in JMESPath
import jmespath.functions
jmespath.functions.TYPES_MAP["IDBObjectWrapper"] = "object"
jmespath.functions.REVERSE_TYPES_MAP["object"] += ("IDBObjectWrapper",)


USER_CONTEXT_WEB_EXT = "userContextIdInternal.webextStorageLocal"


# Based on https://stackoverflow.com/a/24563687/277882
USER_PREF_RE = re.compile(rb"\s*user_pref\(([\"'])(.+?)\1,\s*(.+?)\);")
def read_user_prefs(prefs_path: os.PathLike):
	try:
		with open(prefs_path, "rb") as file:
			for line_no, line in enumerate(file, 1):
				m = USER_PREF_RE.match(line)
				if not m:
					continue
				k, v = m.group(2), m.group(3)
				try:
					k = k.decode("utf-8")
					v = json.loads(v)
				except (ValueError, UnicodeDecodeError) as exc:
					print(f"Failed to parse {prefs_path}:{line_no}: {type(exc).__name__}: {exc}", file=sys.stderr)
				else:
					yield k, v
	except FileNotFoundError:
		pass


def read_user_contexts(profile_dir: pathlib.Path):
	try:
		with open(profile_dir / "containers.json", "rb") as file:
			data = json.load(file)

		assert data["version"] in (4, 5)

		for identity in data["identities"]:
			name = identity.get("name")
			if name is None:
				if data["version"] == 4:
					# `l10nId` example: “userContextPersonal.label” → “personal”
					name = identity["l10nID"].split(".", 1)[0]
					if name.startswith("userContext"):
						name = name[11].lower() + name[12:]
				elif data["version"] == 5:
					# `l10nId` example: “user-context-personal” → “personal”
					name = identity["l10nId"].removeprefix("user-context-")

			yield int(identity["userContextId"]), name
	except (AssertionError, FileNotFoundError, ValueError) as exc:
		print(f"Failed to parse {profile_dir}/containers.json: {type(exc).__name__}: {exc}", file=sys.stderr)
		return 4294967295


@ty.overload
def find_uuid_by_ext_id(profile_dir: pathlib.Path, ext_id: str) -> ty.Optional[str]:
	...

@ty.overload
def find_uuid_by_ext_id(profile_dir: pathlib.Path, ext_id: ty.Iterable[str]) -> ty.List[ty.Optional[str]]:
	...

def find_uuid_by_ext_id(profile_dir: pathlib.Path, ext_id: str | ty.Iterable[str]) \
    -> ty.Optional[str] | ty.List[ty.Optional[str]]:
	for name, value in read_user_prefs(profile_dir / "prefs.js"):
		if name == "extensions.webextensions.uuids":
			try:
				value = json.loads(value)
				if not isinstance(ext_id, str):
					return [value.get(x, None) for x in ext_id]
				else:
					return value.get(ext_id, None)
			except ValueError:
				pass

def find_ext_info(profile_dir: pathlib.Path) -> ty.Iterator[ty.Tuple[str, str]]:
	with open(profile_dir / "extensions.json", "rb") as f:
		ext_data = json.load(f)
	assert ext_data.get("schemaVersion") == 36

	for extension in ext_data["addons"]:
		yield extension["id"], extension["defaultLocale"]["name"]


def find_context_id_by_name(profile_dir: pathlib.Path, name: str) -> int:
	for ctx_id, ctx_name in read_user_contexts(profile_dir):
		if ctx_name == name:
			return ctx_id

	if name == USER_CONTEXT_WEB_EXT:
		return 4294967295  # Default value (-1 as unsigned 32-value)
	else:
		raise KeyError(name)


def find_context_name_by_id(profile_dir: pathlib.Path, id: int) -> str:
	for ctx_id, ctx_name in read_user_contexts(profile_dir):
		if ctx_id == id:
			return ctx_name

	raise KeyError(id)


class IDBObjectWrapper(collections.abc.Mapping):
	def __init__(self, conn: mozidb.IndexedDB):
		self._conn = conn

	def __getitem__(self, name: str) -> object:
		return self._conn.read_object(name)

	def __iter__(self) -> ty.Iterator[object]:
		yield from self._conn.list_objects()

	def __len__(self) -> int:
		return self._conn.count_objects()

	def __repr__(self) -> str:
		inner_repr = ", ".join(repr(k) + ": " + repr(v) for k, v in self.items())
		return f"{{{inner_repr}}}"

	def keys(self) -> ty.List[object]:
		return self._conn.list_objects()

	def items(self) -> ty.Iterable[ty.Tuple[object, object]]:
		return self._conn.read_objects().items()

	def values(self) -> ty.Iterable[object]:
		return self._conn.read_objects().values()


def _safe_repr(object, context, maxlevels, level, sort_dicts):
	"""A repr function that returns more JSON-like output for primitive types

	Code copied from Python 3.9 stdlib pprint.py module.
	"""
	if object is NotImplemented:
		return "undefined", True, False

	typ = type(object)
	if typ in _builtin_scalars:
		# This is the actual patch: Use the JSON library to generate `repr` for
		# all primitive types
		return json.dumps(object, ensure_ascii=False), True, False

	r = getattr(typ, "__repr__", None)
	# Also allow our custom type to be treated as dict
	if issubclass(typ, (dict, IDBObjectWrapper)) and \
	   r in (dict.__repr__, IDBObjectWrapper.__repr__):
		if not object:
			return "{}", True, False
		objid = id(object)
		if maxlevels and level >= maxlevels:
			return "{...}", False, objid in context
		if objid in context:
			return _recursion(object), False, True
		context[objid] = 1
		readable = True
		recursive = False
		components = []
		append = components.append
		level += 1
		if sort_dicts:
			items = sorted(object.items(), key=_safe_tuple)
		else:
			items = object.items()
		for k, v in items:
			krepr, kreadable, krecur = _safe_repr(k, context, maxlevels, level, sort_dicts)
			vrepr, vreadable, vrecur = _safe_repr(v, context, maxlevels, level, sort_dicts)
			append("%s: %s" % (krepr, vrepr))
			readable = readable and kreadable and vreadable
			if krecur or vrecur:
				recursive = True
		del context[objid]
		return "{%s}" % ", ".join(components), readable, recursive

	if (issubclass(typ, list) and r is list.__repr__) or \
	   (issubclass(typ, tuple) and r is tuple.__repr__):
		if issubclass(typ, list):
			if not object:
				return "[]", True, False
			format = "[%s]"
		elif len(object) == 1:
			format = "(%s,)"
		else:
			if not object:
				return "()", True, False
			format = "(%s)"
		objid = id(object)
		if maxlevels and level >= maxlevels:
			return format % "...", False, objid in context
		if objid in context:
			return _recursion(object), False, True
		context[objid] = 1
		readable = True
		recursive = False
		components = []
		append = components.append
		level += 1
		for o in object:
			orepr, oreadable, orecur = _safe_repr(o, context, maxlevels, level, sort_dicts)
			append(orepr)
			if not oreadable:
				readable = False
			if orecur:
				recursive = True
		del context[objid]
		return format % ", ".join(components), readable, recursive

	rep = repr(object)
	return rep, (rep and not rep.startswith("<")), False

_builtin_scalars = frozenset({str, bytes, bytearray, int, float, complex,
                              bool, type(None)})

def _recursion(object):
	return ("<Recursion on %s with id=%s>"
	        % (type(object).__name__, id(object)))

class _safe_key:
	"""Helper function for key functions when sorting unorderable objects.

	The wrapped-object will fallback to a Py2.x style comparison for
	unorderable types (sorting first comparing the type name and then by
	the obj ids).  Does not work recursively, so dict.items() must have
	_safe_key applied to both the key and the value.
	"""

	__slots__ = ["obj"]

	def __init__(self, obj):
		self.obj = obj

	def __lt__(self, other):
		try:
			return self.obj < other.obj
		except TypeError:
			return ((str(type(self.obj)), id(self.obj)) < \
			        (str(type(other.obj)), id(other.obj)))

def _safe_tuple(t):
	"Helper function for comparing 2-tuples"
	return _safe_key(t[0]), _safe_key(t[1])


class PrettyPrinter(pprint.PrettyPrinter):
	def format(self, object, context, maxlevels, level):
		return _safe_repr(object, context, maxlevels, level, self._sort_dicts)

	# Break the maximum line length rules of pprint for strings (for which JSON
	# doesn't support the multiline string concatenation) and all other types
	# that were moded to have a non-default formatting to more closely align
	# with JSON
	_dispatch = pprint.PrettyPrinter._dispatch.copy()
	for tp in (str, bool):
		try:
			del _dispatch[tp.__repr__]
		except (AttributeError, KeyError):
			pass

	# Have our custom type be treated like a regular dict would
	_dispatch[IDBObjectWrapper.__repr__] = pprint.PrettyPrinter._pprint_dict


def find_default_profile_dir() -> ty.Optional[pathlib.Path]:
	# Determine system default Mozilla directory
	import platform
	if platform.win32_ver()[0]:  # Windows
		mozdir = pathlib.Path(os.environ["APPDATA"]) / "Mozilla" / "Firefox"
	elif platform.mac_ver()[0]:  # macOS
		mozdir = pathlib.Path.home() / "Application Support" / "Firefox"
	else:  # Unix/Linux
		mozdir = pathlib.Path.home() / ".mozilla" / "firefox"

	# Attempt to read profile information for Mozilla directory
	from configparser import ConfigParser
	mozini = ConfigParser(interpolation=None)
	mozini.read(mozdir / "profiles.ini")  # silently ignores non-existent files

	# Look for path of default profile directory entry in the parsed profile
	# information
	for s in mozini.sections():
		if not s.startswith("Profile"):
			continue
		if "path" not in mozini[s]:
			continue
		if "default" not in mozini[s]:
			continue

		if mozini[s]["default"] == "1":
			return mozdir / mozini[s]["path"]

	return None


def discover_idbs(sitebase):
	dbs = {}
	for db_path in sitebase.iterdir():
		if not db_path.name.endswith(".sqlite"):
			continue
		with mozidb.IndexedDB(db_path) as conn:
			db_name = conn.get_name()
			if db_name is not None:
				dbs[db_name] = db_path
	return dbs


def to_json(obj):
	# Convert JS object types to basic types
	if isinstance(obj, bool) or isinstance(obj, mozserial.JSBooleanObj):
		return bool(obj)
	elif isinstance(obj, int):
		return int(obj)
	elif isinstance(obj, float):
		return float(obj)
	elif isinstance(obj, str):
		return str(obj)
	elif obj is None or obj is NotImplemented:  # JS undefined → null
		return None
	
	elif isinstance(obj, datetime.datetime):  # datetime → ISO string
		value = obj.astimezone(datetime.timezone.utc).isoformat()
		return value if not value.endswith("+00:00") else value[:-6] + "Z"
	elif isinstance(obj, mozserial.JSRegExpObj):  # JS RegExp → string
		return str(obj)
	
	elif isinstance(obj, list):  # Note: Set isn’t implemented
		return [to_json(item) for item in obj]
	elif isinstance(obj, collections.abc.Mapping):
		return {
			json.dumps(to_json(key)): to_json(value)
			for key, value in obj.items()
			if value is not NotImplemented  # skip `undefined` values entirely
		}
	
	else:
		raise TypeError(f"Cannot JSON-ify {obj!r}")


def resolve_profile_dir(
		parser: argparse.ArgumentParser,
		args: argparse.Namespace,
) -> ty.Tuple[pathlib.Path, pathlib.Path]:
	if args.profile:
		return args.profile, args.profile / "storage" / "default"
	
	profile_path: ty.Optional[pathlib.Path] = find_default_profile_dir()
	if not profile_path or not profile_path.exists():
		parser.error("Could not determine default Firefox profile, pass --profile")
	return profile_path, profile_path / "storage" / "default"


def handle_list_extensions(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
	profile_path, storage_path = resolve_profile_dir(parser, args)
	
	# Special extension storage ID
	ctx_id = find_context_id_by_name(profile_path, USER_CONTEXT_WEB_EXT)
	
	ext_infos = sorted(find_ext_info(profile_path))
	ext_uuids = find_uuid_by_ext_id(profile_path, map(lambda x: x[0], ext_infos))
	
	for (ext_id, ext_name), ext_uuid in zip(ext_infos, ext_uuids):
		db_path = storage_path / f"moz-extension+++{ext_uuid}^userContextId={ctx_id}"
		db_path = db_path / "idb" / "3647222921wleabcEoxlt-eengsairo.sqlite"
		if db_path.exists():
			print("--extension", shlex.quote(ext_id), " #", ext_name)
	return 0


def handle_list_sites(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
	profile_path, storage_path = resolve_profile_dir(parser, args)
	
	# Add sites to list first, so that we can apply sorting before
	# printing them
	sites = []
	for dirpath in storage_path.iterdir():
		if dirpath.name.startswith("moz-extension") or "+++" not in dirpath.name:
			# Extensions have special handling, so skip them here
			continue
		
		if not (dirpath / "idb").is_dir():
			# Skip sites not having any indexed IB stored
			continue
		
		encoded_origin, ctx_name = dirpath.name, ""
		if "^userContextId=" in encoded_origin:
			encoded_origin, ctx_name = encoded_origin.split("^userContextId=", 1)
			try:
				ctx_id = int(ctx_name)
			except ValueError:
				pass  # Keep invalid context IDs as-is
			else:
				try:
					ctx_name = find_context_name_by_id(profile_path, ctx_id)
				except KeyError:
					pass  # Also keep unknown context IDs as-is
		
		scheme, netloc = encoded_origin.split("+++", 1)
		if scheme == "file":
			netloc = netloc.replace("+", "/")
		else:
			netloc = netloc.replace("+", ":")
		origin = scheme + "://" + netloc
		
		dbs = list(discover_idbs(storage_path / dirpath.name / "idb"))
		sites.append((origin, ctx_name, dbs))
	sites.sort()
	
	# Print sorted list of sites with their user-context if applicable
	for origin, ctx_name, dbs in sites:
		for db_name in dbs:
			if ctx_name:
				print("--site", shlex.quote(origin), "--userctx", shlex.quote(ctx_name),
				      "--sdb", shlex.quote(db_name))
			else:
				print("--site", shlex.quote(origin), "--sdb", shlex.quote(db_name))
	
	return 0


def handle_read(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
	profile_path, storage_path = resolve_profile_dir(parser, args)
	db_path: ty.Optional[pathlib.Path] = args.dbpath
	
	ctx_id = 0  # Use default
	if args.userctx:
		try:
			ctx_id = int(args.userctx)
		except ValueError:
			ctx_id = find_context_id_by_name(profile_path, args.userctx)
	
	# Collect required extra data for figuring out extension paths
	if args.extension:
		# Map extension ID to browser internal UUID
		ext_uuid = find_uuid_by_ext_id(profile_path, args.extension)
		if ext_uuid is None:
			print(f"Failed to look up internal UUID for extension ID: {ext_uuid} (is the extension installed?)", file=sys.stderr)
			return 1
		
		# Use special extension storage ID if no other was set
		if userctx == 0:
			ctx_id = find_context_id_by_name(profile_path, USER_CONTEXT_WEB_EXT)
		
		origin_label = f"moz-extension+++{ext_uuid}"
		
		if not db_path:
			if ctx_id:
				origin_label += f"^userContextId={ctx_id}"
			
			db_path = storage_path / origin_label
			db_path = db_path / "idb" / "3647222921wleabcEoxlt-eengsairo.sqlite"
	elif args.site:
		site_name = args.site.replace(":", "+").replace("/", "+")
		if ctx_id != 0:
			site_name += f"^userContextId={ctx_id}"
		
		site_base = storage_path / site_name / "idb"
		if not site_base.is_dir():
			parser.error("Invalid --site given (pass --list-sites to list)")
			return 1
		
		db_path = site_base / args.sdb
		if not db_path.is_file():
			dbs = discover_idbs(site_base)
			if args.sdb in dbs:
				db_path = dbs[args.sdb]
		if not db_path.exists():
			parser.error("Invalid --sdb given (omit --sdb with --site to list)")
	else:
		if not db_path.is_file():
			parser.error("Invalid --dbpath given")
	
	print(f"Using database path: {db_path}", file=sys.stderr)
	
	with mozidb.IndexedDB(db_path) as conn:
		value = jmespath.search(args.key_name, IDBObjectWrapper(conn))
		if args.output == "full":
			pretty_printer = PrettyPrinter()
			pretty_printer.pprint(value)
		else:  # JSON
			json.dump(to_json(value), sys.stdout, ensure_ascii=False, indent="\t")
	
	return 0


def main(argv=sys.argv[1:], program=sys.argv[0]) -> int:
	# Global parameters
	parser = argparse.ArgumentParser(description=__doc__, prog=pathlib.Path(program).name)
	parser.add_argument("-V", "--version", action="version", version="%(prog)s {0}".format(__version__))
	parser.add_argument("-profile", "--profile", metavar="PROFILE", type=pathlib.Path,
	                    help="Path to the Firefox/MozTK application profile directory.")
	
	# Specific parser actions:
	subparsers = parser.add_subparsers(required=True)
	
	#  → List all extensions in profile directory
	subparser_lexts = subparsers.add_parser(
		"list-extensions", help="Lists all known extensions in the profile directory."
	)
	subparser_lexts.set_defaults(handler=handle_list_extensions)
	
	#  → List all websites in profile directory
	subparser_lsites = subparsers.add_parser(
		"list-sites", help="Lists all sites in the profile directory with their IDBs."
	)
	subparser_lsites.set_defaults(handler=handle_list_sites)
	
	#  → Read value(s) – structured or JSON
	def add_read_args(subparser: argparse.ArgumentParser):
		subparser.add_argument(
			"-x", "--extension", action="store", metavar="EXT_ID",
			help="Use database of the extension with the given Extension ID."
		)
		subparser.add_argument(
			"-s", "--site", action="store", metavar="SITE_NAME",
			help="Name of the site returned by the `list-sites` command."
		)
		subparser.add_argument(
			"-S", "--sdb", action="store", metavar="DB_NAME",
			help="Name of the database returned by the `list-site-dbs` command."
		)
		subparser.add_argument(
			"--dbpath", action="store", metavar="DB_PATH", type=pathlib.Path,
			help="Use database file with the the given path.")
		subparser.add_argument(
			"--userctx", action="store",
			help="Use given user context (“Firefox container”) when determining the "
			     "database path."
		)
		subparser.add_argument(
			"key_name", metavar="KEY", default="@", nargs="?",
			help="JMESPath of the key to query."
		)
	
	subparser_read = subparsers.add_parser(
		"read", help="Reads a value (possibly containing further values) belonging "
		             "to the specified site or extension in a faithful "
		             "representation that is NOT VALID JSON.")
	subparser_read.set_defaults(handler=handle_read, output="full")
	add_read_args(subparser_read)
	
	subparser_read_json = subparsers.add_parser(
		"read-json", help="Reads a value (possibly containing further values) belonging "
		                  "to the specified site or extension in a reduced "
		                  "JSON-compatible representation missing some details.")
	subparser_read_json.set_defaults(handler=handle_read, output="json")
	add_read_args(subparser_read_json)
	

	
	# Parse command-line arguments using `argparse`
	args = parser.parse_args(argv)
	
	# Special condition checking: Mutual dependency between --sdb and --site
	if args.handler is handle_read:
		if args.sdb and not args.site:
			parser.error("argument --site is required when using --sdb")
			return 1
		
		if args.site and not args.sdb:
			parser.error("argument --sdb is required when using --site")
			return 1
	
	# Dispatch to handler (calls the `.set_defaults(handler=…)` from above)
	return args.handler(parser, args)


if __name__ == "__main__":
	sys.exit(main())
