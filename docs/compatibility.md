# Changes from the research document

The catalog (`catalog/sections.yaml`) follows the team's *Multi-OS Hardening Script
Recipe Builder* document. These are the places where the implementation changes
or adds to it so the same choices work on all five targets, and to keep a
hardening run from locking anyone out.

## Cross-cutting

| Change | Why |
| --- | --- |
| Scripts run as a **dry run by default**; `--apply` makes changes | Lets you review exactly what will change before it happens. |
| Every edited file is **backed up** to `/var/backups/hardeningchef/<time>/`, and all actions are logged to `/var/log/hardeningchef.log` | Lets you undo changes and see what was done. |
| The script **checks `/etc/os-release`** and refuses to run on the wrong OS family (or the wrong version, unless you pass `--force`) | A Fedora script on Debian would install the wrong tools. |
| **Package hardening runs last** | Disabling a source first would break the AppArmor, ufw and logging installs that follow it. |
| Prompts read from `/dev/tty`, and `--yes` answers them | Prompts still work when the script is piped from `curl`. |

## SSH

* The settings go in **`sshd_config.d/00-hardeningchef.conf`**. OpenSSH keeps the *first* value it reads for each setting, and `00-` sorts before `50-redhat.conf` (Fedora/Rocky) and `50-cloud-init.conf` (Ubuntu cloud images). That puts our file at the highest priority, which is what the doc asks for. The script also makes sure `sshd_config` includes the drop-in directory.
* Added **`KbdInteractiveAuthentication no`**. Without it, PAM's keyboard-interactive login still accepts passwords even when `PasswordAuthentication no` is set.
* **Lockout guard:** `PasswordAuthentication` stays `yes`, with a warning, if no admin user has an `authorized_keys` file. The web form can install keys for a new admin account in the same run.
* The script validates the new config with `sshd -t` and restores the old one if it fails. It then reloads `ssh` (Debian family) or `sshd` (Fedora/Rocky), and handles Ubuntu's socket-activated `ssh.socket`.
* Kali does not ship a running SSH server, so there's an option to install it.

## Accounts

* **Admin group** is `sudo` on Debian/Ubuntu/Kali and `wheel` on Fedora/Rocky. The user running the script is never removed from it. Sudo rules that grant access outside the group are reported but not edited.
* **pam_faillock:** on Debian-family systems it is enabled through `pam-auth-update` profiles, not hand-edited PAM files. On Fedora/Rocky it uses `authselect enable-feature with-faillock`.
* **Minimum length** is enforced with pam_pwquality (`minlen` in `/etc/security/pwquality.conf`). The script installs `libpam-pwquality` on Debian-family systems.
* **Inactive accounts:** the shadow `INACTIVE` field counts days after a password *expires*, not days since the last login, so it can't do what the doc describes. Instead, a daily systemd timer (`hc-inactive-accounts`) reads last-login times from `lastlog2`, which Debian 13, Ubuntu 26.04 and Fedora now use, or from `lastlog`. It then expires non-admin accounts over the limit.
* **umask** is set in `login.defs`, `/etc/profile.d`, and the umask lines in Fedora/Rocky's `/etc/bashrc` and `/etc/profile`, which would otherwise override it.

## Firewall

* **ufw on Debian/Ubuntu/Kali, firewalld on Fedora/Rocky.** ufw isn't packaged in Rocky's base repositories, and fighting the OS's native firewall causes conflicts. The 0-4 actions from the doc work the same on both:
  * firewalld rules go in a dedicated `hardeningchef` zone, set as the default zone. This avoids reusing Fedora Workstation's zone, which opens ports 1025-65535.
  * Outbound rules use a firewalld policy (`HOST -> ANY`).
* Added a **default outgoing policy** option. With the default "allow", the "allow out" actions only matter if you switch it to "deny".
* **Extra ports** (`port/proto:action`) cover the doc's "can be easily expanded".
* The script refuses to apply a firewall that blocks SSH while you're connected over SSH, unless you pass `--force`.

## Package management

* APT sources are disabled in place: `Enabled: no` for deb822 `.sources` files (Ubuntu 26.04, Debian 13 and Kali all use these now), and commented-out lines for `.list` files.
* DNF repositories are disabled with `dnf config-manager setopt` on dnf5 (Fedora), and by editing the `.repo` file on dnf4 (Rocky).
* **Removing packages with no source** skips kernels and packages marked Essential, required or important. It also refuses (unless you pass `--force`) when more than 25 packages qualify, which usually means a main repository was disabled. The website warns when you choose to disable the distribution's main source.
* The FUSE 2 package is `libfuse2t64` on current Debian-family releases (after the 64-bit `time_t` transition) and `fuse-libs` on Fedora/Rocky.
* Flatpak is blocked from coming back with an APT pin (`Pin-Priority: -1`) or with DNF's `excludepkgs`.

## AppArmor / SELinux

* GRUB is only changed if the running kernel doesn't already have AppArmor active. AppArmor is on by default on Ubuntu, Debian and Kali, so the change is usually skipped.
* Since Fedora 34 / EL9, SELinux is disabled with the `selinux=0` kernel argument, not in `/etc/selinux/config`. If SELinux is disabled, the script:
  1. removes that argument with `grubby`
  2. sets the mode to **permissive**
  3. schedules a relabel
  4. asks you to re-run the script after the reboot to switch to enforcing

  This is the order Red Hat recommends.

## Logging

* Debian 13 and Fedora log only to the systemd journal by default. Most of the classic `/var/log` text files (`auth.log`, `syslog`, `secure`, `messages`) don't exist unless rsyslog is installed. `hc-logs` therefore lists journal views (authentication, SSH, kernel, firewall blocks, errors) next to the files that do exist, and rsyslog is an optional install.
* There's also an option to keep the journal across reboots.
