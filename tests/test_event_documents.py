#!/usr/bin/env python3
"""Public Sheet document links and the combined Event Notes API."""
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src/scripts'), str(ROOT/'tests')]
import event_documents as documents  # noqa: E402
import test_event_notes as notes  # noqa: E402

LINKS = [
    {'title': 'Rulebook', 'url': 'https://docs.google.com/document/d/DEMO/edit?usp=sharing#heading=h.rules'},
    {'title': 'Event Document', 'url': 'https://docs.google.com/document/d/e/DEMO/pub'},
    {'title': 'PDF briefing', 'url': 'https://example.test/briefing.pdf#page=3'},
]


def t_parser_preserves_public_links_and_sheet_order():
    source = 'Title,URL\n' + '\n'.join(row['title']+','+row['url'] for row in LINKS)
    assert documents.parse_event_documents(source) == LINKS
    assert documents.public_link('  http://example.test/briefing.pdf  ') == 'http://example.test/briefing.pdf'


def t_headers_and_empty_rows():
    assert documents.parse_event_documents('\ufeff Link, NAME \nhttps://example.test/rules,Rulebook\n') == [
        {'title': 'Rulebook', 'url': 'https://example.test/rules'}]
    for source in ['', 'Title,URL\n', 'Heading,Note\nHello,Text\n', '<html>unavailable</html>',
                   'Title,URL\nOnly title\n,url\nNo link,\n']:
        assert documents.parse_event_documents(source) == [], source
    assert documents.parse_event_documents('Title,URL\n"Briefing, round 4",https://example.test/a.pdf\n')[0]['title'] == 'Briefing, round 4'


def t_non_browser_urls_are_skipped():
    for value in [None, '', 'javascript:alert(1)', 'data:text/html,hello', 'file:///tmp/notes.pdf',
                  '/relative.pdf', '//example.test/relative', 'https://', 'https://example.test:bad/',
                  'https://example.test:70000/', 'https://user:password@example.test/',
                  'https://bad host/briefing.pdf',
                  'https://example.test/\nbriefing.pdf', 'https://example.test/\tbriefing.pdf']:
        assert not documents.public_link(value), value
    assert documents.parse_event_documents('Title,URL\nBad,javascript:alert(1)\nGood,https://example.test/a\n') == [
        {'title': 'Good', 'url': 'https://example.test/a'}]


def t_refresh_fetches_only_the_sheet_and_keeps_last_good_links_on_network_error():
    m = notes.m
    source = m.EventDocumentsSource('https://example.test/sheet.csv')
    with patch.object(m, 'urlopen', return_value=io.BytesIO(b'Title,URL\nRulebook,https://example.test/rules.pdf\n')) as fetch:
        assert source.refresh()
        assert fetch.call_count == 1
        assert fetch.call_args.args[0].full_url == source.csv_url
    before = source.get()
    with patch.object(source, '_fetch_text', return_value=None):
        assert not source.refresh() and source.get() == before
    with patch.object(source, '_fetch_text', return_value='Title,URL\n'):
        assert source.refresh() and source.get() == []
    normal = m.EventNotesSource(None)
    with patch.object(normal, '_fetch_text', return_value='Heading,Note,Priority\nRules,Keep space,Important\n'):
        assert normal.refresh() and normal.get()[0]['priority'] == 'important'


def t_document_only_sheets_keep_the_existing_entry_available():
    source = notes.m.EventDocumentsSource(None); source.rows = LINKS
    server, get = notes._event_notes_client(None, source)
    try:
        status, body = get('/event-notes/data')
        assert status == 200 and body == {'available': True, 'notes': [], 'documents': LINKS}
        source.rows = []
        assert get('/event-notes/data')[1] == {'available': False, 'notes': [], 'documents': []}
    finally:
        server.shutdown(); server.server_close()


def t_notes_and_documents_have_independent_lists():
    text = notes.m.EventNotesSource(None); text.rows = [{'heading': 'Brief', 'note': 'Sponsor read', 'priority': 'important'}]
    links = notes.m.EventDocumentsSource(None); links.rows = LINKS
    server, get = notes._event_notes_client(text, links)
    try:
        status, body = get('/event-notes/data')
        assert status == 200 and body['notes'] == text.rows and body['documents'] == LINKS
        assert json.loads(json.dumps(body)) == body
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('t_') and callable(fn):
            fn(); print('ok', name)
    print('ALL PASS')
