# moz-idb-edit

IndexedDB reader (and perhaps one day also writer) for Firefox and other MozTK applications.

## Installation

Recommended installation is via [pipx](https://pypa.github.io/pipx/):

```py
pipx install git+https://gitlab.com/ntninja/moz-idb-edit.git
```

## JSON output mode

The allowed datastructures serialized in an indexed DB form a strict superset
of those specified in JSON. While the default `read` command attempts to print
a faithful representation of all allowed (and implemented) datastructures, the
`read-json` command applies verious transformations to reduce the data to valid
JSON:

 * JS Basic objects → basic types  (ie: `new String("TEXT")` → `"TEXT"`)
 * Date type → ISO date string (ie: `new Date(2024, 2, 1, 10, 51, 6)` → `"2024-02-01T10:51:06Z"`)
 * undefined → null, except when used as object entry value then instead drop the key
 * Map object → JSON object (ie: `new Map()` → `{}`) with all keys stringified
 * BigInt object/type → JSON number (ie: `BigInt(5)` → `5`)
 * RegExp object → RegExp string (ie: `/abc/g` → `"/abc/g"`)