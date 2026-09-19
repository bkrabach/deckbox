"""Static presentation for Deckbox's local CA trust bootstrap."""

from __future__ import annotations


def render_setup_page(ca_fingerprint: str | None) -> str:
    """Render the self-contained local-CA trust instructions."""
    download = ""
    fingerprint = """
      <p>
        Deckbox has not prepared a local CA certificate yet. The operator must
        run <code>deckbox setup-tls</code>, then reload this page.
      </p>
    """
    if ca_fingerprint is not None:
        download = """
      <p><a class="download" href="/ca.crt" download="deckbox-ca.crt">Download deckbox-ca.crt</a></p>
        """
        fingerprint = f"""
      <p>
        SHA-256 fingerprint:
        <code>{ca_fingerprint}</code>
      </p>
      <p>
        Compare this fingerprint out-of-band with
        <code>deckbox setup-tls --status</code> or <code>deckbox doctor</code>
        before trusting the certificate.
      </p>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Deckbox certificate setup</title>
  <style>
    body {{ background: #f5f7fa; color: #1f2937; font-family: system-ui, sans-serif; line-height: 1.5; margin: 0; }}
    main {{ background: white; border-radius: 12px; box-shadow: 0 4px 18px #0002; margin: 3rem auto; max-width: 44rem; padding: 2rem; }}
    code {{ background: #edf2f7; border-radius: 4px; overflow-wrap: anywhere; padding: .15rem .35rem; }}
    .download {{ background: #2563eb; border-radius: 6px; color: white; display: inline-block; padding: .65rem 1rem; text-decoration: none; }}
  </style>
</head>
<body>
  <main>
    <h1>Deckbox certificate setup</h1>
    <p>
      The download contains the public CA only; it contains no private keys.
      Deckbox does not publish its server certificate or any configuration.
    </p>
    {download}
    {fingerprint}
    <h2>Trust on macOS</h2>
    <ol>
      <li>Download <code>deckbox-ca.crt</code>.</li>
      <li>Open Keychain Access and import the certificate into the System keychain.</li>
      <li>Open the certificate, expand Trust, and choose Always Trust.</li>
      <li>Fully quit your browser and perform a full restart before opening Deckbox again.</li>
    </ol>
    <p>Terminal alternative:</p>
    <pre><code>sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain deckbox-ca.crt</code></pre>
  </main>
</body>
</html>
"""
