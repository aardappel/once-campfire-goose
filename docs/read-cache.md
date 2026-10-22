# Read-cache validation

Each HTTP worker owns its page cache. Entries store the authenticated user ID, exact request
target and complete header bytes, followed by the plain and gzip bodies. Comparing the two
request slices directly avoids allocating a formatted key on a hit. Different headers may
cause extra misses; they cannot accidentally select another request's cached response.

The ordinary path opens a SQLite read transaction, reads `PRAGMA data_version`, and performs
authentication, authorization and rendering against that snapshot. A changed version clears
both page and authentication caches. Mutating HTTP requests clear the worker cache as well.

On Linux, the pinned SQLite shim can certify that a previously completed read transaction's
database state is still current. It reads both copies of SQLite 3.54.1's `WalIndexHdr` through
the connection's VFS shared-memory mapping, with SQLite's read order and memory barrier.
The copies must match, have the expected format and initialization flag, and pass the native
header checksum. The complete 48-byte header is compared, including both salts and the frame
number, so WAL restarts and writes from other processes invalidate the certificate.

A certificate is installed only when the header is unchanged before and after the complete
ordinary read path. It expires at the next wall-clock second. A fast hit also requires the
same authenticated cookie, an unexpired signed-cookie timestamp, and an exact cached request.
Changed, unavailable or inconsistent WAL state goes through SQL again. Unknown WAL layouts
disable this path. The certificate never authorizes a newly seen request or extends a cookie's
expiry. SQLite's locking and write durability settings are unchanged.

The design follows SQLite's `walIndexTryHdr` protocol; Jai's published
[WAL generation check](https://github.com/lucasmeijer/once-campfire-jai/blob/2ecd487/src/framework/db/database.jai)
also demonstrates using that protocol for response replay. This implementation retains the
existing Goose HTTP response path and uses a small C adapter for SQLite's opaque VFS handles.

With `CAMPFIRE_CACHE_DIAGNOSTICS=1`, `X-Campfire-Snapshot: wal` identifies a certified hit;
`transaction` identifies the ordinary path. The HTTP regression suite verifies the fast path
actually executes, then exercises cookie expiry, external edits and revocation, uncommitted
and rolled-back writes, and a truncated/restarted WAL.
