"""Auditable reading text. Never change the raw ASR or invent missing course content."""
from collections import Counter
import re

FILLER = re.compile(r'^[\s。，,！？!?…嗯啊呃哦唉]+$')
STUTTER = re.compile(r'(这个|那个|然后|所以|我们|就是|可以|因为|如果|得到|发现|形成|相当于|大家)(?:[，,\s]*\1)+')
TOPIC = re.compile(r'^(下面|接下来|现在来看|再来看|我们再看|另一方面|回到)')


def clean_spoken(text):
    text = re.sub(r'\s+', ' ', text).strip()
    if not text or FILLER.fullmatch(text):
        return ''
    text = re.sub(r'^(?:嗯|啊|呃)[，,。\s]+', '', text)
    text = re.sub(r'(?<=[，,。；;])\s*(?:嗯|啊|呃)[，,\s]+', '', text)
    # Remove trailing conversational tags, but keep standalone answers and real questions.
    text = re.sub(r'[，,]\s*(?:对吧|是吧)[？?，,。]?', '，', text)
    text = re.sub(r'^(?:对吧|是吧)[？?，,。]\s*', '', text)
    text = STUTTER.sub(r'\1', text)
    text = re.sub(r'([我你他她它这那就都])\1+', r'\1', text)
    text = re.sub(r'(没有|不能|不)(?:\1)+', r'\1', text)
    return re.sub(r'([，。！？；])\1+', r'\1', text).strip()


def reading_paragraphs(segments, *, target_chars=240, max_chars=420):
    paragraphs, edits, current = [], [], None
    for index, row in enumerate(segments):
        text = clean_spoken(row['text'])
        if text != row['text']:
            edits.append({'segment_id': index, 'before': row['text'], 'after': text,
                          'reason': 'filler_only' if not text else 'light_cleanup'})
        if not text:
            continue
        boundary = current is not None and (
            row['start'] - current['end'] >= 3 or
            len(current['text']) + len(text) > max_chars or
            len(current['text']) >= target_chars and current['text'].endswith(('。', '！', '？', ';', '；')) or
            len(current['text']) >= 100 and TOPIC.match(text))
        if boundary:
            paragraphs.append(current)
            current = None
        if current is None:
            current = {'start': row['start'], 'end': row['end'], 'text': text, 'segment_ids': [index]}
        else:
            separator = ' ' if current['text'][-1:].isascii() and text[:1].isascii() else ''
            current['text'] += separator + text
            current['end'] = row['end']
            current['segment_ids'].append(index)
    if current is not None:
        paragraphs.append(current)
    return paragraphs, edits


def protected_tokens(text):
    """Conservative token precheck, not a test of semantic equivalence."""
    text = re.sub(r'(没有|不能|不)(?:\1)+', r'\1', text)
    conditions = Counter({word: text.count(word) for word in ('不', '没有', '不能', '如果', '只有', '除非', '至少', '至多', '大于', '小于')})
    canonical = text.replace('_', '')
    for spaced, symbol in ((r'U\s*G\s*S\s*T\s*H', 'UGSTH'), (r'U\s*G\s*S', 'UGS'),
                           (r'U\s*D\s*S', 'UDS'), (r'U\s*G\s*D', 'UGD'), (r'I\s*D', 'ID')):
        canonical = re.sub(spaced, symbol, canonical)
    canonical = re.sub(r'V\s*([一二12])', lambda match: 'V'+{'一':'1','二':'2'}.get(match[1], match[1]), canonical)
    identifiers = r'[A-Za-z][A-Za-z0-9]*'
    units = r'厘米|毫米|分钟|安培|欧姆|伏|瓦|秒'
    anchors = re.findall(identifiers+r'|[\u0370-\u03ff]|[-+−]?\d+(?:\.\d+)?(?:%|％)?|'+units, canonical)
    return anchors, conditions


def apply_review(paragraphs, reviewed):
    """Explicit edits only. Mechanical validation is not a factual-accuracy guarantee."""
    if not isinstance(reviewed, list) or len(reviewed) != len(paragraphs):
        raise ValueError('Review must preserve every source paragraph')
    output, edits = [], []
    for source, item in zip(paragraphs, reviewed):
        if (not isinstance(item, dict) or not {'segment_ids', 'text'} <= set(item) or
                set(item)-{'segment_ids', 'text', 'issues'}):
            raise ValueError('Invalid review record')
        if (item['segment_ids'] != source['segment_ids'] or
                not isinstance(item['segment_ids'], list) or any(type(i) is not int for i in item['segment_ids']) or
                not isinstance(item['text'], str) or not item['text'].strip()):
            raise ValueError('Review changed source mapping or removed content')
        issues = item.get('issues', [])
        if not isinstance(issues, list) or len(issues) > 10:
            raise ValueError('Invalid review issues')
        retained = source['text']
        issue_edits = []
        for issue in issues:
            if (not isinstance(issue, dict) or set(issue) != {'source_text', 'reason'} or
                    any(not isinstance(issue[k], str) or not issue[k].strip() for k in issue) or
                    len(issue['reason']) > 500 or issue['source_text'] not in retained):
                raise ValueError('An issue must quote an exact source span')
            quote = issue['source_text']
            start = retained.index(quote)
            end = start+len(quote)
            term = (re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', quote) and
                    not re.search(r'[A-Za-z0-9_-]$', retained[:start]) and
                    not re.match(r'[A-Za-z0-9_-]', retained[end:]))
            clause = (len(quote) >= 6 and (start == 0 or retained[start-1] in '。，,；;！？.!?') and
                      (end == len(retained) or retained[end] in '。，,；;！？.!?'))
            if quote.lower() in ('not', 'no', 'never', 'if', 'unless', 'only') or not (term or clause):
                raise ValueError('Defer a complete unclear clause or term, never just a negation or number')
            retained = retained.replace(issue['source_text'], '', 1)
            issue_edits.append({'reason': 'uncertain_content', **{k:source[k] for k in ('segment_ids', 'start', 'end')},
                                'source_text': issue['source_text'], 'detail': issue['reason']})
        if protected_tokens(retained) != protected_tokens(item['text']):
            raise ValueError('Review changed protected token order or logical conditions')
        if len(item['text']) > max(100, len(source['text']) * 1.6):
            raise ValueError('Review expanded into unsupported exposition')
        output.append({**source, 'text': item['text'].strip()})
        edits.extend(issue_edits)
        if source['text'] != item['text']:
            edits.append({'segment_ids': source['segment_ids'], 'before': source['text'], 'after': item['text'],
                          'reason': 'explicit_review'})
    return output, edits


def stamp(value):
    value = int(value)
    return f'{value//3600:02}:{value//60%60:02}:{value%60:02}'


def markdown(paragraphs, title='课堂阅读稿'):
    lines = [f'# {title}', '']
    for row in paragraphs:
        lines.extend((f'**{stamp(row["start"])}–{stamp(row["end"])}**', '', row['text'], ''))
    return '\n'.join(lines)


def issues_markdown(issues):
    lines = ['# 疑点记录', '']
    for issue in issues:
        lines.extend((f'**{stamp(issue["start"])}–{stamp(issue["end"])}**', '',
                      issue['detail'], '', '> '+issue['source_text'].replace('\n', '\n> '), ''))
    if not issues:
        lines.extend(('暂无单独记录的疑点。', ''))
    return '\n'.join(lines)
