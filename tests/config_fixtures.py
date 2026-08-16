"""Shared Kerio configuration XML for tests.

Shaped like the package postinst output. Hosts and usernames are examples,
never values from a live install.
"""

from __future__ import annotations

EXAMPLE_FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
EXAMPLE_PASSWORD_XOR = "XOR:3a3b3c"

POSTINST_XML = """\
<config>
  <connections>
    <connection type="persistent">
      <server>vpn.example.com</server>
      <port>4090</port>
      <username>someone</username>
      <password>XOR:3a3b3c</password>
      <fingerprint>AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99</fingerprint>
      <active>1</active>
    </connection>
  </connections>
</config>
"""
