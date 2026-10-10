# deploy/home

The PA VM on the devbox: an Incus VM, `pa-home`, that reaches the public internet and nothing of the owner's. See the Home hosting bullet in the [roadmap](../../docs/roadmaps/v0.1-prototype.md).

## Setting up

Run the wizard on the devbox. It walks through the steps only the owner can do and calls the scripts below for the rest. Each stage checks its result, and re-running it skips what's done.

```sh
deploy/home/setup-wizard.sh
```

| Script | Does |
|---|---|
| `incus-setup.sh` | Creates the `pa` btrfs storage pool (copy-on-write, so snapshots are cheap) and the VM's own bridge, `pabr0`, with IPv6 off |
| `install-firewall.sh` | Installs the bridge firewall (`firewall/`) as `pa-vm-firewall.service` |
| `create-vm.sh` | Creates `pa-home` from `profile.yaml` (4 vCPU, 6 GiB RAM, 40 GiB disk, cloud-init, user `assistant`, Tailscale) |
| `update.sh <ref>` | Snapshots the VM, then puts the repo at `<ref>` and runs `uv sync`; the first run installs uv and Claude Code |
| `rollback.sh [snapshot]` | Restores the VM to a snapshot, by default the latest one `update.sh` took |
| `check-isolation.sh` | Probes the fences from inside the VM |

`tailscale-policy.hujson` holds the parts of the tailnet policy the VM needs, for merging into the policy in the admin console.

## The fences

- **Host firewall** (`firewall/pa-vm.nft`): its own nftables table on the bridge. Anything the VM sends to the devbox, at any of its addresses, is dropped except DNS and DHCP. Anything it sends on to private ranges (RFC1918, which covers the LAN and Docker's networks), the tailnet range (100.64.0.0/10) or link-local is dropped, and so is all IPv6. A drop in this table is final whatever Incus's, Docker's or Tailscale's own rules accept.
- **Tailnet policy**: the VM joins as `tag:pa`. The owner's devices may reach it, and it is the source of no rule. Its traffic to tailnet peers travels inside Tailscale's tunnel, so only the tailnet policy can stop it.

The devbox also runs ufw, which drops input and forwarded traffic by default. `install-firewall.sh` adds ufw rules letting the bridge's DHCP, DNS and outbound traffic through, and the `pa_vm` table still fences everything else.

The firewall loads at boot from `pa-vm-firewall.service`, after Docker and before Incus. Incus requires it, so if the rules fail to load, Incus doesn't start and the VM stays down. Don't enable `nftables.service` to persist rules: Ubuntu's `/etc/nftables.conf` starts with `flush ruleset`, which would wipe Docker's and Incus's rules at boot.

## Updating and rolling back

```sh
deploy/home/update.sh <commit|branch|tag>   # snapshot, then deploy
deploy/home/rollback.sh                     # back to the latest pre-update snapshot
incus snapshot list pa-home                 # every snapshot
```

The VM's Tailscale identity lives on its disk, so it survives reboots and rollbacks to any snapshot taken after it joined.

## Acceptance run (#27)

1. Run the wizard from nothing; it ends with `check-isolation.sh` passing.
2. From the Mac, `ssh assistant@pa-home` gets a shell.
3. Reboot the devbox. `pa-home` comes back on its own, `systemctl status pa-vm-firewall` is active, and `check-isolation.sh` passes again.
4. Roll back:
   ```sh
   deploy/home/update.sh <ref>
   incus exec pa-home -- touch /root/rollback-marker
   deploy/home/rollback.sh
   incus exec pa-home -- test ! -e /root/rollback-marker && echo "rolled back"
   ```
