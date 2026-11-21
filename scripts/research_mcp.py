"""Bundled trusted STDIO public RSS search. No shell, accounts or arbitrary tools."""
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlsplit
from mcp.server.mcpserver import MCPServer
from core.teaching_import import fetch

server = MCPServer('Jarvis public research')


@server.tool()
def public_search(query: str, limit: int=3) -> str:
    """Read public Bing RSS search results for the owner's personal RSS research."""
    if not 2 <= len(query) <= 300 or type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('Use a short public query and 1-5 results')
    data, mime = fetch('https://www.bing.com/search?' + urlencode({'format':'rss','q':query}))
    root = ET.fromstring(data)
    results = []
    for item in root.findall('./channel/item')[:limit]:
        url = item.findtext('link', '')
        results.append({'url':url, 'title':item.findtext('title','')[:300],
                        'excerpt':item.findtext('description','')[:1600],
                        'publisher':urlsplit(url).hostname,
                        'published_at':None,'provider_date':item.findtext('pubDate'),
                        'date_note':'RSS item date, not a confirmed publisher publication date',
                        'provider':'Bing personal RSS via bundled MCP'})
    return json.dumps({'results':results,'use':'Personal non-commercial RSS research; publisher rights retained'})


@server.tool()
def public_browser(url: str, scope_id: str='', action: str='read', text: str='') -> str:
    """Use the installed isolated browser; account actions require owner standing scope."""
    from core.research_browser import browser
    return json.dumps(browser(url,scope_id or None,action,text))


if __name__ == '__main__':
    from core import storage,users
    if os.environ.get('JARVIS_RESEARCH_DATA_DIR'):
        storage.DATA=Path(os.environ['JARVIS_RESEARCH_DATA_DIR']).resolve()
    users.set_current(os.environ.get('JARVIS_RESEARCH_USER','owner'))
    server.run(transport='stdio')
