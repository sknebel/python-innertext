from __future__ import unicode_literals

import sys
import bs4
import re

from bs4.element import Tag, NavigableString, Comment

if sys.version < '3':
    from urlparse import urljoin
    text_type = unicode
    binary_type = str
else:
    from urllib.parse import urljoin
    text_type = str
    binary_type = bytes
    basestring = str


_whitespace_to_space_regex = re.compile(r"[\n\t\r]+")
_reduce_spaces_regex = re.compile(r" {2,}")



def try_urljoin(base, url, allow_fragments=True):
    """attempts urljoin, on ValueError passes through url. Shortcuts http(s):// urls"""
    if url.startswith(("https://", "http://")):
        return url
    try:
        url = urljoin(base, url, allow_fragments=allow_fragments)
    except ValueError:
        pass
    return url


def get_attr(el, attr, check_name=None):
    """Get the attribute of an element if it exists.

    Args:
      el (bs4.element.Tag): a DOM element
      attr (string): the attribute to get
      check_name (string or list, optional): a list/tuple of strings or single
        string, that must match the element's tag name

    Returns:
      string: the attribute's value
    """
    if check_name is None:
        return el.get(attr)
    if isinstance(check_name, basestring) and el.name == check_name:
        return el.get(attr)
    if isinstance(check_name, (tuple, list)) and el.name in check_name:
        return el.get(attr)


def get_img_src_alt(img, dict_class, img_with_alt, base_url=''):
    """given a img element, returns both src and alt attributes as a list of tuples if alt exists, else returns the src as a string
    use for alt parsing with img
    """

    alt = get_attr(img, "alt", check_name="img")
    src = get_attr(img, "src", check_name="img")

    if src is not None:
        src = try_urljoin(base_url, src)

        if alt is None or not img_with_alt:
            return text_type(src)
        else:
            return dict_class([
                                ("value", text_type(src)),
                                ("alt", text_type(alt))
                            ])

def get_children(node):
    """An iterator over the immediate children tags of this tag"""
    for child in node.children:
        if isinstance(child, bs4.Tag) and child.name != 'template':
            yield child


def get_descendents(node):
    """An iterator over the all children tags (descendants) of this tag"""
    for desc in node.descendants:
        if isinstance(desc, bs4.Tag) and desc.name != 'template':
            yield desc



def is_rendered(node):
    """decides if a node is rendered by filtering elements that are display: none in default stylesheets from https://html.spec.whatwg.org/multipage/rendering.html#the-css-user-agent-style-sheet-and-presentational-hints"""
    # Comments
    if isinstance(node, Comment):
        return False
    #dialog:not([open]) 
    if node.name == "dialog" and not node.has_attr("open"):
        return False
    #:matches(table, thead, tbody, tfoot, tr) > form 
    if node.name == "form" and node.parent.name in ('table', 'thead', 'tbody', 'tfoot', 'tr'):
        return False
    #[hidden]
    if isinstance(node, Tag) and node.has_attr("hidden"):
        return False
    #area, base, basefont, datalist, head, link, meta, noembed,
    #noframes, param, rp, script, source, style, template, track, title
    if node.name in ("area", "base", "basefont", "datalist", "head", "link", "meta", "noembed", "noframes", "param", "rp", "script", "source", "style", "template", "track", "title"):
        return False
    
    return True


BLOCK_BEGIN = -1
BLOCK_END = -2

_remove_whitespace_before_segment_break = re.compile(r"[ \t]+\n")
_remove_whitespace_after_segment_break = re.compile(r"\n[ \t]+")
_collapse_newlines = re.compile(r"\n{2,}")
_remove_ZWS_segment_breaks = re.compile(r"\n*\u200b\n*")
_reduce_spaces_regex = re.compile(r" {2,}")

def segment_break_transformation(s):
    """implements segment break handling according to https://drafts.csswg.org/css-text/#line-break-transform"""
    #TODO: this ignores BIDI characters for now
    #TODO: this does not do language specific handling
    s = _collapse_newlines.sub("\n", s)
    s = _remove_ZWS_segment_breaks.sub("\u200b", s)
    return s

def do_whitespace_internal(l):
    """implements whitespace handling according to https://drafts.csswg.org/css-text/#white-space-phase-1"""
    #TODO: this ignores BIDI characters for now
    #TODO: this does not do language specific handling
    
    s = "".join(l)
    s = _remove_whitespace_before_segment_break.sub("\n", s)
    s = _remove_whitespace_after_segment_break.sub("\n", s)
    s = segment_break_transformation(s)
    s = s.replace("\t", " ")
    s = s.replace("\n", " ")
    s = _reduce_spaces_regex.sub(" ", s)
    return s.strip(" ")


def do_2whitespace(items):
    
    output_items = []
    l=[]
    skip_to_block_end = False
    for item in items:
        if not skip_to_block_end:
            if isinstance(item, text_type):
                l.append(item)
            else:
                output_items.append(do_whitespace_internal(l))
                output_items.append(item)
                l=[]
                if item == BLOCK_BEGIN:
                    skip_to_block_end = True
        else:
            output_items.append(item)
            if item == BLOCK_END:
                skip_to_block_end = False
    
    output_items.append(do_whitespace_internal(l))
    return output_items




def do_whitespace(items):
    
    output_items = []
    collected=[]
    skip_to_block_end = 0
    
    for item in items:
        if not skip_to_block_end:
            if isinstance(item, text_type):
                collected.append(item)
            else:
                if collected:
                    output_items.append(do_whitespace_internal(collected))
                    collected=[]
        else:
            if item not in (BLOCK_BEGIN,BLOCK_END):
                output_items.append(item)
            
        if item == BLOCK_BEGIN:
            skip_to_block_end += 1
            output_items.append(item)
            
        if item == BLOCK_END:
            skip_to_block_end -= 1
            output_items.append(item)    
    
    output_items.append(do_whitespace_internal(collected))
    return output_items


def is_pre_rendered(el):
    return el.name in ('listing', 'plaintext', 'pre', 'xmp')



def inner_text_collection(el, replace_img=True, img_to_src=False, base_url='', pre_mode=False, required_line_break_count=1, first=False):
    """implements inner text collection steps (mostly) as in https://html.spec.whatwg.org/multipage/dom.html#inner-text-collection-steps"""

    # Compared to the WHATWG spec, we do not parse CSS and thus instead implement rules based on the default stylesheet specified

    items = []

    #  
    # ADDED: since we do not parse CSS white-space property, we assume it from common tags
    if is_pre_rendered(el):
        pre_mode=True

    # "The inner text collection steps, given a node node, are as follows:
    #
    # 1. Let items be the result of running the inner text collection steps with each child node of node in tree order, and then concatenating the results to a single list."
    if not isinstance(el, NavigableString):
        for child in el.children:
            child_items = inner_text_collection(child, replace_img, img_to_src, base_url, pre_mode, required_line_break_count)
            items.extend(child_items)
    # "2. If node's computed value of 'visibility' is not 'visible', then return items."
    # we do not compute visibility, and there is no element that by default has visibility set to none.
    # some are set to collapsed when hidden, but they are also covered by the next step
    
    # "3. If node is not being rendered, then return items.
    # For the purpose of this step, the following elements must act as described if the computed value of the 'display' property is not 'none':
    # ..."  -- 3 special cases we skip for now
    # if the element has default display: none, all children have it too (https://drafts.csswg.org/css2/visuren.html#display-prop), so the child list should actually be empty to

    # TODO: this can be pulled up to not needlessly collect children of invisible objects
    if not first and not is_rendered(el):
        items = []
        return items
    # "4. If node is a Text node node, then for each CSS text box produced by node, in content order, compute the text of the box after application of the CSS 'white-space'
    # processing rules and 'text-transform' rules, set items to the list of the resulting strings, and return items. […]"
    # We do NOT do whitespace processing here, since the CSS specification describes it happening over larger groups of items
    # We do assume that only a single box is created (e.g. ::before, ::after CSS pseudo selectors, which could create additional boxes, are not relevant to us)
    
    if isinstance(el, NavigableString):
        box = text_type(el)
        items = [box]
        return items

    # "5. If node is a br element, then append a string containing a single U+000A LINE FEED (LF) character to items."
    # We additional
    if el.name == 'br':
        items = [BLOCK_BEGIN, '\n', BLOCK_END]

    #6/7: table styling: skip

    # "8. If node is a p element, then append 2 (a required line break count) at the beginning and end of items."
    # We by default use a required line break count of 1, since this was preferred by consumers
    if el.name == 'p':
        items = [required_line_break_count] + items
        items.append(required_line_break_count)

    # microformats-parsing specific image handling
    if el.name == 'img' and replace_img:
        value = el.get('alt')
        if value is None and img_to_src:
            value = el.get('src')
            if value is not None:
                value = try_urljoin(base_url, value)

        if value is not None:
            items = [" "+text_type(value)+" "]
    
    #If node's used value of 'display' is block-level or 'table-caption', then append 1 (a required line break count) at the beginning and end of items. [CSSDISPLAY]
    #list of block level elements from MDN
    # we do 
    if el.name in ('address', 'article', 'aside', 'blockquote', 'details', 'dialog', 'dd', 'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hgroup', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'table', 'ul'):
        if not pre_mode:
            items = do_whitespace(items)
        items = [BLOCK_BEGIN, 1] + items + [1, BLOCK_END]
    
    return items





def get_textContent(el, replace_img=True, img_to_src=True, base_url=''):
    """ Get the text content of an element, replacing images by alt or src
    """
    x = inner_text_collection(el, replace_img, img_to_src, base_url, first=True)
    if not is_pre_rendered(el):
        x = do_whitespace(x)
    results = [t for t in x if t  not in ('', BLOCK_BEGIN, BLOCK_END)]

    if results:
        # remove leading whitespace and <int> i.e. next lines
        while isinstance(results[0], int):
            results.pop(0)
            if not results:
                break

    if results:
        # remove trailing whitespace and <int> i.e. next lines
        while isinstance(results[-1], int):
            results.pop(-1)
            if not results:
                break

    # create final string by concatenating replacing consecutive sequence of <int> by largest value number of \n
    count = 0
    for i,t in enumerate(results):
        if isinstance(t, int):
            if t>count:
                results[i]="\n"*(t-count)
                count=t
            else:
                results[i]=""
        else:
            count=0
    return "".join(results)


soup = bs4.BeautifulSoup("""<!doctype html>
<html>
  <head></head>
  <body>
    <h1>Hi!</h1>
    <p>Lipsum</p>
    <p>Lipsum <a href="">agnoangs</a> and <span>more <span> more</span></span> final more.</p>
    <p>Lipsum <a href="">agnoangs</a> and <span>more <br><span> more</span></span> final more.</p>
    <pre>
      String first.
      <p>Now a string in a P.</p>
      <p>And another one in a P.</p>
      And a string to end.
    </pre>
    Just a string!
    <div><p>Footer?</p></div>
  </body>
</html>""", features="lxml")

import glob
def run_tests():
    DIR = "test/files/"
    for html_filename in glob.glob(DIR+"*.html"):
        with open(html_filename) as f:
            print(html_filename)
            soup=bs4.BeautifulSoup(f, features="html5lib")
            s=get_textContent(soup.find(id="innertexttest"))
            with open(html_filename[:-4]+"txt") as f2:
                s2=f2.read()
                try:
                    assert s2 == s
                except:
                    print(repr(s2))
                    print(repr(s))
        
