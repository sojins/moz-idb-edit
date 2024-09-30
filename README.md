# moz-idb-edit

IndexedDB reader (and perhaps one day also writer) for Firefox and other MozTK applications.

## Installation

Recommended installation is via [pipx](https://pypa.github.io/pipx/):

```py
pipx install git+https://gitlab.com/ntninja/moz-idb-edit.git
```

## Usage

### Listing available site databases / extensions in default Firefox profile

After installation use the `list-extensions` or `list-sites` commands to list
all available IndexedDBs:

```shell
$ moz-idb-edit list-extensions
--extension @testpilot-containers  # Firefox Multi-Account Containers
--extension CookieAutoDelete@kennydo.com  # Cookie AutoDelete
--extension uBlock0@raymondhill.net  # uBlock Origin
--extension '{3c078156-979c-498b-8990-85f7987dd929}'  # Sidebery
--extension '{e4a8a97b-f2ed-450b-b12d-ee082ba24781}'  # Greasemonkey
…

$ moz-idb-edit list-sites
--site https://account.proton.me --userctx personal --sdb mutex
--site https://gitlab.com --sdb vscode-web-db
--site https://gitlab.com --sdb vscode-web-state-db-global
--site https://html-classic.itch.zone --userctx personal --sdb /home/web_user/.renpy
--site https://lemmy.blahaj.zone --userctx personal --sdb workbox-expiration
--site https://mail.proton.me --userctx personal --sdb mutex
--site https://mail.proton.me --userctx personal --sdb store
--site https://mynixos.com --sdb mnos
…
```

Note that only sites that have actually created an IndexedDB are listed
(one entry for every IDB created by the site), sites only using cookies or local
storage are skipped since those features are not implemented using an IndexedDB.
For extensions, the commonly used
[`browser.storage.local`](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/storage/local)
API is implemented using an IndexedDB, so most extensions will be listed.

### Displaying database contents

Each of the lines printed by the `list-*` commands can be used as arguments to
the `read` command to display the entire contents of the database:

```shell
$ moz-idb-edit read --site https://account.proton.me --userctx personal --sdb mutex  # empty database
Using database path: /home/user/.mozilla/firefox/Default/storage/default/https+++account.proton.me^userContextId=1/idb/208203099mxuet.sqlite
{}

$ moz-idb-edit read --site https://gitlab.com --sdb vscode-web-state-db-global
Using database path: /home/user/.mozilla/firefox/Default/storage/default/https+++gitlab.com/idb/4277777170vlsacboodleg--wbedb--.sqlite
{"__$__isNewStorageMarker": "true",
 "__$__targetStorageMarker": "{\"sync.previous.store\":1,\"storage.serviceMachineId\":1,\"sync.machine-session-id\":1,\"sync.user-session-id\":1,\"workbench.panel.markers.hidden\":0,\"workbench.panel.output.hidden\":0,\"terminal.hidden\":0,\"workbench.explorer.views.state.hidden\":0,\"workbench.scm.views.state.hidden\":0,\"workbench.view.search.state.hidden\":0,\"workbench.activityBar.location\":0,\"memento/notebookEditors\":1,\"workbench.activity.pinnedViewlets2\":0,\"workbench.activity.placeholderViewlets\":1,\"workbench.panel.pinnedPanels\":0,\"workbench.panel.placeholderPanels\":1,\"recently.opened\":0,\"memento/customEditors\":1,\"productIconThemeData\":1,\"colorThemeData\":0,\"iconThemeData\":1,\"sync.storeUrl\":1,\"sync.productQuality\":1,\"workbench.view.debug.state.hidden\":0,\"workbench.welcomePage.walkthroughMetadata\":0,\"chat.workspaceTransfer\":1,\"userDataSyncAccountPreference\":1,\"userDataSyncAccount.donotUseWorkbenchSession\":1,\"notifications.perSourceDoNotDisturbMode\":1,\"memento/gettingStartedService\":0,\"userDataSyncAccountProvider\":1,\"editorOverrideService.cache\":1,\"settings.lastSyncUserData\":1,\"keybindings.lastSyncUserData\":1,\"extensionStorage.migrate.gitlab.gitlab-workflow-GitLab.gitlab-workflow\":1,\"snippets.lastSyncUserData\":1,\"themeUpdatedNotificationShown\":0,\"tasks.lastSyncUserData\":1,\"gitlab-web-ide-ntninja-usages\":1,\"globalState.lastSyncUserData\":1,\"profiles.lastSyncUserData\":1,\"sync.lastSyncTime\":1,\"sync.sessionId\":1,\"workbench.view.extension.gitlab-duo.state.hidden\":0,\"gitlab.gitlab-web-ide\":1}",
 "chat.workspaceTransfer": "[]",
 …
```

Please note that the printed format **is not JSON** but rather a JSON superset
that attempts to faithfully model all the allowed items in an IndexedDB, to
print the output in a reduced JSON-compatible representation use the separate
`read-json` command instead.

The `read` command also accepts [JMESPath](https://jmespath.org/) specifications
to pre-filter the output; while its not [`jq`](https://jqlang.github.io/jq/) it
does allow performing many useful queries (and most importantly actually exists
as a usable Python package).

For example, to display only the keys of the selected IndexedDB:

```shell
$ moz-idb-edit read --site https://gitlab.com --sdb vscode-web-state-db-global 'keys(@)'
Using database path: /home/user/.mozilla/firefox/Default/storage/default/https+++gitlab.com/idb/4277777170vlsacboodleg--wbedb--.sqlite
["__$__isNewStorageMarker",
 "__$__targetStorageMarker",
 "chat.workspaceTransfer",
 "colorThemeData",
 "editorOverrideService.cache",
 "extensionStorage.migrate.gitlab.gitlab-workflow-GitLab.gitlab-workflow",
 "gitlab-web-ide-ntninja-usages",
 "gitlab.gitlab-web-ide",
 "globalState.lastSyncUserData",
…
```

## JSON output mode

The allowed datastructures serialized in an indexed DB form a strict superset
of those specified in JSON. While the default `read` command attempts to print
a faithful representation of all allowed (and implemented) datastructures, the
`read-json` command applies various transformations to reduce the data to valid
JSON:

| From                          | To                    | Example                         |
| ----------------------------- | --------------------- | ------------------------------- |
| Object-wrapped primitive type | Base primitive type   | `new String("TEXT")` → `"TEXT"` |
| `Date` object                 | ISO date string (UTC) | `new Date(2024, 2, 1, 10, 51, 6)` → `"2024-02-01T10:51:06Z"` |
| `undefined`                   | *Dropped* if used as an Object/Map value, `null` otherwise | `{"A": undefined, "B": [undefined]}` → `{"B": [null]}` |
| `Map` object                  | JSON Object, with all keys stringified | `new Map([[1, 2]])` → `{"1": 2}` |
| `BigInt` object/type          | JSON number           | `BigInt(5)` → `5`               |
| `RegExp` object               | RegExp string         | `/abc/g` → `"/abc/g"`           |
