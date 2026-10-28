"""SWINGPULSE TERMINAL branding — permanent app title, workspace/section
page headings and per-section subtitles across both workspaces."""
import os
import re

SRC = open(os.path.join(os.path.dirname(__file__), '..', 'dashboard.py')).read()

# Extract JS object keys from a `PAGE_SUBTITLES` block and section ids from NAV.
def _block(name):
    m = re.search(name + r'\s*=\s*\{(.+?)\};', SRC, re.S)
    return m.group(1) if m else ''

def _keys(block):
    return set(re.findall(r"\{\s*id:'([a-z]+)'", block)) or set(
        re.findall(r"([a-z]+)\s*:", block))


class TestAppBranding:
    def test_title_tag_is_swingpulse(self):
        assert '<title>SWINGPULSE TERMINAL</title>' in SRC

    def test_header_shows_permanent_title_and_subtitle(self):
        assert 'SWINGPULSE TERMINAL' in SRC
        assert 'AI-Powered Trading &amp; Portfolio Intelligence' in SRC

    def test_old_title_removed(self):
        assert 'AI Swing Trading Bot' not in SRC

    def test_page_heading_element_exists(self):
        assert 'id="page-heading-title"' in SRC
        assert 'id="page-heading-sub"' in SRC

    def test_document_title_carries_brand(self):
        assert 'SWINGPULSE TERMINAL — ' in SRC


class TestHeadingConvention:
    def test_title_is_workspace_arrow_section(self):
        assert "wsName=NAV_STATE.ws==='intraday'?'Intraday':'Swing'" in SRC
        assert '`${wsName} → ${sec.label}`' in SRC
        assert '`${wsName} / ${sec.label}`' not in SRC

    def test_default_heading_uses_arrow(self):
        assert '>Swing → Overview<' in SRC
        assert 'Swing Trading /' not in SRC

    def test_every_nav_section_has_subtitle(self):
        nav_block = _block('const NAV')
        sub_block = _block('const PAGE_SUBTITLES')
        nav_ids = set(re.findall(r"\{id:'([a-z]+)'", nav_block))
        sub_ids = set(re.findall(r"^\s+([a-z]+)\s*:", sub_block, re.M))
        missing = nav_ids - sub_ids
        assert not missing, f"sections without subtitle: {missing}"

    def test_subtitles_match_spec(self):
        for txt in (
            'Portfolio Overview & Trading Dashboard',
            'Live IPO Market Data, Subscription Trends & Risk Assessment',
            'Intraday Market Overview & PAPER Trading Dashboard',
            'ORB/VWAP Signals & Trading Opportunities',
            'Angel One Connectivity & Monitoring',
        ):
            assert txt in SRC


class TestUntouched:
    def test_workspace_badges_preserved(self):
        assert 'ws-mode live">LIVE' in SRC
        assert 'ws-mode paper">PAPER' in SRC

    def test_nav_structure_unchanged(self):
        # Section/tab ids must be identical to the frozen baseline
        for frag in ("{id:'overview'", "{id:'ipo'", "{id:'assistant'",
                     "itab:'itab-overview'", "itab:'itab-risk'"):
            assert frag in SRC

    def test_status_fields_preserved(self):
        for el in ('hdr-last-scan', 'hdr-engine-cycle', 'hdr-status',
                   'hdr-workspace', 'hdr-token'):
            assert f'id="{el}"' in SRC
