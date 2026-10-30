# HTTP listener startup

Issue #1033 reports repeated macOS test delays. The standard HTTP server resolves
its bound address through `socket.getfqdn()` even when the caller supplied a
numeric address. Racecast handlers need the bound address and port, not its DNS
name. Listener startup must therefore bind through `TCPServer` and populate HTTP
metadata from the actual socket address. Forward resolution of a configured
hostname remains the socket's responsibility.

| Requirement | Path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| No reverse DNS at bind | Shared server, relay, Control Center | Real HTTP status response with bound port | Reverse lookup raises immediately | macOS CI startup profile and suite duration |
| Frozen imports | Binary build and path-loaded UI | Frozen UI serves its identity ping | Missing from-import peer fails the packaging guard | Native binary smoke tests |
| Preserve bind scope | Shared server | Loopback and explicit wildcard keep their socket addresses | No implicit wildcard substitution | Real local listeners |
| Preserve bind failure | Shared server | Ephemeral port assigned | Occupied listener raises EADDRINUSE | Two listeners contend for one port |
| Preserve HTTP errors and teardown | Shared server | 200 response and listener shutdown | 404 response retains error body | Real local requests and joined worker |

Implementation sequence:

1. Profile relay import, standard loopback binding and the Solo tests on macOS CI.
2. Observe the DNS regression failing, then add a shared DNS-free HTTP server.
3. Use that server in the relay, Control Center and local HTTP test fixtures.
4. Falsify the new guarantees, review code and security, run the full suite and build.
5. Remove temporary CI profiling, compare runner durations and merge after green CI.

The HTTP server's `server_name` contains its numeric bound address. The operating
system still owns bind errors and address resolution. Request routing, listener
scope and authentication do not depend on reverse DNS. The stdlib server remains
unmodified for other consumers in the process.

Frozen builds declare `http_server` as a hidden import. The path-loaded UI
packaging guard checks both `import` and `from ... import` dependencies.
