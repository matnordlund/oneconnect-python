# Running oneconnect-python with SELinux enabled

Applies to Fedora/RHEL (and derivatives) with SELinux's targeted policy —
this isn't relevant on Debian/Ubuntu, which use AppArmor by default.

This isn't always needed: on a fresh Fedora 44 Workstation install, a
plain `connect`/`disconnect` worked with zero `vpnc_t` denials under full
enforcing, no custom module required. Try it first — only follow the
steps below if you actually hit a denial.

Requires `audit` and `policycoreutils-python-utils` (provides `audit2allow`;
`policycoreutils` itself provides `semodule`/`setenforce`):

```bash
sudo dnf install audit policycoreutils policycoreutils-python-utils
```

1. Set permissive mode (denials get logged, nothing gets blocked):

   ```bash
   sudo setenforce 0
   ```

2. Set up and verify the VPN normally while permissive — add your profile,
   connect, disconnect. Don't move on until it actually works end-to-end.

3. Turn whatever got logged into an allow policy and load it. Include
   `user_avc` alongside `avc` — D-Bus-mediated denials (e.g. polkit
   registering an authentication agent) log as `user_avc`, not `avc`, and
   `audit2allow` will silently miss them otherwise:

   ```bash
   sudo ausearch -m avc,user_avc -ts recent | audit2allow -M oneconnect
   sudo semodule -i oneconnect.pp
   ```

   `-ts recent` only looks back about 10 minutes, so if step 2 took longer
   than that, use `-ts today` instead to catch everything logged so far
   today.

4. Turn enforcing back on and retest:

   ```bash
   sudo setenforce 1
   ```

5. If `sudo ausearch -m avc,user_avc -ts recent` shows anything new, repeat
   step 3 (rerunning with `-M oneconnect` extends the existing module).
