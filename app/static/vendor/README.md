# Vendored front-end libraries

Chart.js is loaded locally (no CDN) so the dashboard works on a Raspberry Pi
without internet access.

`scripts/development_mac.sh` and `scripts/install_raspberry.sh` download it
automatically. To do it by hand:

```bash
curl -L -o app/static/vendor/chart.umd.min.js \
  https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js
```

The dashboard degrades gracefully (tables + numbers, no line charts) if the file
is missing.
