# ILO4Unlock FanTrainer

Small Python CLI to automate fan presets and temperature-based fan curves on
an HP/HPE server whose iLO4 has been unlocked with
[kendallgoto/ilo4_unlock](https://github.com/kendallgoto/ilo4_unlock), giving
SSH access to the `fan` command.

> **Safety warning:** overriding HP's fan control can cause your server to
> overheat if misconfigured. Start with conservative presets, watch
> temperatures closely, and keep the `stock` preset (`fan g start`) handy to
> restore default behavior. Use at your own risk — see the upstream
> [ilo4_unlock](https://github.com/kendallgoto/ilo4_unlock) project for the
> full legal disclaimer.

## Setup

1. Create a dedicated, low-privilege iLO account for automation (no extra
   iLO privileges are needed for `fan` commands).
2. Install dependencies:
   ```powershell
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your iLO host/credentials:
   ```
   ILO_HOST=192.168.1.50
   ILO_USERNAME=fanctrl
   ILO_PASSWORD=changeme
   ```

> **Every new terminal, activate the venv first** (`venv\Scripts\activate` on
> Windows) before running `python ilofan.py ...`. If you run a bare `python`
> that resolves to a different/global install, it likely has a newer paramiko
> (4.x/5.x) that **cannot talk to iLO4 at all** (`ValueError: unknown cipher`)
> because it dropped the legacy SHA-1 KEX/DSA algorithms iLO4 requires — see
> [requirements.txt](requirements.txt). Check with:
> `python -c "import paramiko,sys; print(sys.executable, paramiko.__version__)"`
> — it should print a path inside `venv\` and version `3.x`. If it doesn't,
> activate the venv (or call `venv\Scripts\python.exe ilofan.py ...` directly).
   An SSH key can be used instead of a password via `ILO_SSH_KEY_PATH` (+
   `ILO_SSH_KEY_PASSPHRASE` if needed).

## Usage

Run a raw command (useful for exploring your server's fan layout first):

```powershell
python ilofan.py run "fan info"
```

List and apply presets (defined in [config/presets.yaml](config/presets.yaml)):

```powershell
python ilofan.py preset list
python ilofan.py preset apply quiet --dry-run   # preview commands first
python ilofan.py preset apply quiet
python ilofan.py preset apply stock             # restore HP default control
```

Apply a temperature-based fan curve once, or continuously monitor and adjust
(defined in [config/curve.yaml](config/curve.yaml)):

```powershell
python ilofan.py curve apply --dry-run
python ilofan.py curve apply
python ilofan.py curve monitor --interval 30
```

Before trusting `curve monitor`, calibrate the temperature parser against
your own hardware:

```powershell
python ilofan.py debug temps
```

This prints the raw `fan info t` output next to what got parsed, so you can
adjust `temperature_sensors` in `curve.yaml` (or the regex in
[ilo_fan/curve.py](ilo_fan/curve.py)) if the values look wrong.

## How it works

- [ilo_fan/ssh_client.py](ilo_fan/ssh_client.py) opens a single SSH transport
  configured to offer the legacy KEX/host-key algorithms
  (`diffie-hellman-group14-sha1`, `ssh-rsa`/`ssh-dss`) that iLO4's SSH server
  requires, then runs each `fan` command over its own channel.
- [ilo_fan/presets.py](ilo_fan/presets.py) applies a named list of raw `fan`
  commands from `config/presets.yaml`.
- [ilo_fan/curve.py](ilo_fan/curve.py) reads `fan info t`, picks the highest
  watched temperature, linearly interpolates a PWM percent from
  `config/curve.yaml`'s control points, and applies it with
  `fan p <channel> max <percent>`.

### Command units (from the unlocked iLO4 `fan` CLI)

- `fan p <n> min|max <percent>` — percentage 0-100 (one decimal allowed).
- `fan pid <n> sp|lo|hi <value>` — temperature × 100 (e.g. `5500` = 55.00°C).
- `fan t <n> off` — disable a temperature sensor's influence on fan control.
- `fan g start` — restore HP's default automatic fan control.

Run `python ilofan.py run "fan info"` on your own server to see exactly which
PWM channels, PID loops, and temperature sensors it exposes before writing
your own presets/curves.

## Known issue: `fan` commands can return no output (or be silently ignored)

Some unlocked iLO4 firmware builds have an **unfixed upstream bug** where the
`fan` command is accepted (no "COMMAND NOT RECOGNIZED") but produces **no SSH
output at all** — `help`/`power`/`version` work normally in the same session,
but `fan info`, `fan info t`, etc. return nothing, even after long waits or
retries. See [ilo4_unlock#51](https://github.com/kendallgoto/ilo4_unlock/issues/51)
and [ilo4_unlock#52](https://github.com/kendallgoto/ilo4_unlock/issues/52) (the
latter reports that `fan pid`/`fan p` *set* commands can also be silently
ignored on some hardware, not just unreadable — i.e. the fan floor may not
actually change).

Run `python ilofan.py debug session` to check for this on your server: it
runs `help`, `power`, and `fan info` back to back so you can see whether only
`fan` output is missing.

If you hit this:

- **Don't trust `curve monitor`** — it depends on reading `fan info t`, which
  cannot work if `fan` output is empty.
- **Verify `preset apply` actually changes fan behavior** before relying on
  it — listen for an audible fan speed change, or check the iLO web UI's
  system health/fan page, since the SSH session may not confirm success even
  when a command *does* work. Do not assume it applied just because the tool
  ran without error.
- Check whether a newer iLO4 firmware / different patch build resolves it
  (see the linked issues); this is a firmware-level problem outside this
  tool's control.

