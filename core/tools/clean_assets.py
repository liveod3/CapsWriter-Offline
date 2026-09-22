
import sys
from pathlib import Path

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.i18n import tr, initialize_tool_language

if __name__ == '__main__':
    initialize_tool_language()
from importlib.util import find_spec
relies = ['markdown_it', 'rich']
if not all([find_spec(x) for x in relies]):
    print(tr('terminal.clean_assets.this_script_requires_markdown_it_and_rich_install'))
    input(tr('terminal.clean_assets.press_enter_to_exit'))

import re
import os
from os import remove
from typing import List
from pprint import pprint
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token
from rich.console import Console

console = Console(highlight=0, soft_wrap=False)

markdown_ext = ['md', 'markdown']
asset_ext = ['jpg', 'jpeg', 'png', 'wav', 'mp3', 'mp4']



def get_md_files(path):         # Find Markdown files recursively beneath the input directory.
    p = Path(path)
    if not p.exists(): return []
    if not p.is_dir(): return []

    md_files = []
    for ext in markdown_ext:
        md_files.extend(list(p.glob("**/*." + ext)))

    return md_files


def get_links(text: str):       # Find links in the document.
    links = []

    def add_link(token: Token):
        if 'src' in token.attrs:
            links.append(unquote(token.attrs['src']))
        if 'href' in token.attrs:
            links.append(unquote(token.attrs['href']))
        if token.type == 'html_inline':
            m = re.match(r'.*src="(.+?)".*', token.content)
            if m: links.append(unquote(m.group(1)))
        elif token.type == 'text':
            for m in re.finditer(r'.*?\[\[(.+?)\]\]', token.content):
                links.append(unquote(m.group(1)))
        if token.children is None: return
        for t in token.children:
            add_link(t)

    md = (MarkdownIt('commonmark' ,{'breaks':True,'html':True}))
    # pprint(md.parse(text))
    for t in md.parse(text):
        add_link(t)

    return links


def absolutify_links(file, links: List[str]):   # Check that the link targets a local file.
    if type(file) is not Path: file = Path(file)
    
    temp_links = links.copy(); links.clear()
    for link in temp_links:
        if (file.parent / link).exists():
            links.append(file.parent / link)
            continue
        elif Path(link).exists():
            links.append(link)


def main():
    # Default to the current directory.
    root = Path(__file__).parent

    # Use a supplied directory when present.
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.exists() and p.is_dir(): root = p
    console.print(tr('terminal.clean_assets.yellow_this_script_recursively_removes_image_and_audio'))
    console.print(tr('terminal.clean_assets.green_cleanup_root', value0=root))
    console.input(tr('terminal.clean_assets.green_press_enter_to_search_for_markdown_files'))

    # Collect Markdown files.
    md_files = get_md_files(root)
    console.print(tr('terminal.clean_assets.green_markdown_files_found'))
    for f in md_files:console.print(f'    {f}')
    console.line()
    console.input(tr('terminal.clean_assets.green_press_enter_to_search_for_unreferenced_attachments'))

    # Collect referenced attachments.
    links_used = []
    for md in md_files:
        with open(md, "r", encoding="utf-8") as f: text = f.read()
        links = get_links(text)
        absolutify_links(md, links)
        links_used.extend(links)

    # Collect attachment directories.
    folders = set(l.parent for l in links_used)

    # Collect attachment files.
    links_all = []
    for folder in folders:
        if not folder.is_relative_to(root): continue
        for markdown_ext in asset_ext:
            links_all.extend(list(folder.glob("**/*." + markdown_ext)))
    links_all = list(set(links_all))
    
    # Find unreferenced attachments.
    links_unused = set(links_all) - set(links_used)
    console.print(tr('terminal.clean_assets.yellow_unreferenced_attachments_found'))
    for file in sorted(links_unused):
        console.print(f'    {file}')
    for i in range(3):
        if console.input(tr('terminal.clean_assets.yellow_to_confirm_deletion_type_delete_and_press')) == 'delete': break
    else:
        console.print(tr('terminal.clean_assets.red_confirmation_not_received_after_three_attempts_exiting'))
        sys.exit()
    
    # Delete unreferenced files.
    console.print(tr('terminal.clean_assets.red_deleting_files'))
    for f in links_unused:
        remove(f); console.print(f'    [red]{f}')
    console.input(tr('terminal.clean_assets.green_cleanup_complete_press_enter_to_exit'))


if __name__ == "__main__":
    main()
