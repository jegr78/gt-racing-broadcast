# Driver telemetry: the driver guide

Drivers can send their own GT7 telemetry to the broadcast. A small program, the
**sender**, runs on a device on the same network as the PlayStation, receives the
console's telemetry and passes it to the producer's relay. It sends nothing else, and
it does not change what the PlayStation or your other apps do.

![The invite page](images/driver-invite-page.png)

## Get your command

1. Open the invite link the producer shares for the event.
2. Pick your entry and, in an endurance race, your stint or qualifying segment.
3. Type your name and press **Get my sender command**.

The page shows a command with your personal code. The code works for the whole event.
When the producer renews the invite link, open the new link and register again.

## Which setup

GT7 sends telemetry to every device that asks for it, but on one device only one
program can receive it. Pick the line that matches what you already run:

- **Nothing else receives your telemetry:** run the command as shown, on a PC, laptop,
  tablet or phone.
- **A dashboard app on your phone:** run the sender on a second device on the same
  network. It cannot share the phone with the app.
- **SimHub on the same PC:** add `--forward 127.0.0.1:PORT`. The sender then passes every
  packet to that port; point SimHub's GT7 input at it and turn SimHub's own heartbeat off.
- **GT Race Engineer with "Telemetry Forwarding":** let it forward to a port on the same
  device and add `--listen PORT`. The sender then sends no heartbeat of its own.

The sender finds the PlayStation by itself. If it reports "no console found", add
`--console <PlayStation IP>`; the PlayStation's network settings show its IP address.

## Your platform

Every platform needs the same thing from its firewall: **allow inbound UDP 33740** for
the sender (or the `--listen` port). The console sends from a port of its own, so its
packets do not count as an answer to the sender and a firewall drops them by default.

- **Windows.** With Python 3.8 or newer from python.org, download
  `racecast-sender.py` from the invite page and run the Python command in a terminal.
  Without Python, download the Windows program from the invite page and run the second
  command in the folder you saved it to. The program is not signed: SmartScreen shows
  "Windows protected your PC", choose **More info → Run anyway**. When Windows asks
  whether to allow the program on networks, allow **private networks**.
- **macOS.** Python 3 from python.org, or the macOS program from the invite page. For
  the program, run `chmod +x racecast-sender-macos` once. It is not signed: macOS refuses
  the first start, then open **System Settings → Privacy & Security** and choose **Open
  Anyway**. Allow incoming connections when the firewall asks.
- **Linux.** Run the Python command. With ufw: `sudo ufw allow 33740/udp`.
- **Android (Termux).** Install Termux, run `pkg install python`, download the script
  with `curl -O <script link from the invite page>`, then run the Python command. Run
  `termux-wake-lock` first so Android does not pause Termux.
- **iPhone and iPad (a-Shell).** Download the script with `curl -O <script link>` and
  run the Python command. iOS runs a-Shell only in the foreground: keep the screen on,
  a-Shell in front and the device charging.

## While it runs

The sender prints a status line every ten seconds: packets per second received from the
console and batches sent to the relay. It says when the console goes quiet (menus,
replays, network) and when the upload is interrupted, and it catches up after a short
outage. Stop it with Ctrl+C.

The producer sees every sender with your entry and name, and can disconnect one. If the
sender says your entry already has a sender, someone else registered for it; ask the
producer.

For other client developers: the ingest is a versioned HTTP contract,
[`src/docs/design/driver-telemetry-ingest.md`](https://github.com/jegr78/gt-racing-broadcast/blob/main/src/docs/design/driver-telemetry-ingest.md).
