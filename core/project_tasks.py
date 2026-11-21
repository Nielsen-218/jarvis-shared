"""Trusted unfamiliar project contracts and test data. Never copied into the lab."""
import random

FILES=('parser.py','ledger.py','solution.py')
TASKS={
 'word_totals': '''Build a three-file Python project: parser.py exports parse(text) as valid (category,word) string pairs, ledger.py exports aggregate(records), solution.py imports and composes them in solve(text). Input has newline-separated category:word records. Strip whitespace around both fields. Ignore blanks, stripped # comments, malformed rows with anything other than exactly one colon, and rows with either field empty. Count repeated valid rows individually. Return {category: {"characters": total Python len(word) across its valid rows, "count": number of valid rows}}. Unicode words and internal spaces are valid. Empty input returns {}. Use loops. No filesystem, network or subprocess use.''',
 'word_peaks': '''Build a three-file Python project: parser.py exports parse(text) as valid (category,word) string pairs, ledger.py exports aggregate(records), solution.py imports and composes them in solve(text). Input has newline-separated category=word records. Strip fields. Ignore blanks, stripped # comments, malformed rows with anything other than exactly one equals sign, and rows with either field empty. Count repeated valid rows individually. Return {category: {"longest": largest Python len(word) for its valid rows, "count": number of valid rows}}. Unicode words and internal spaces are valid. Empty input returns {}. Use loops. No filesystem, network or subprocess use.''',
 'loop_totals': '''Build a three-file Python project with parser.py parse(text), ledger.py aggregate(records), solution.py solve(text), importing between the modules. Input contains newline-separated category:integer rows. Strip whitespace around fields. Ignore blanks, stripped # comment lines and invalid rows. Valid rows have exactly one colon, nonempty category and an integer matching ASCII [+-]?[0-9]+. Return a dictionary mapping each category to {"total": sum of all accepted integers, "count": number of accepted rows}. Repeats count individually; retain categories with total zero. Empty input returns {}. Use loops over parsed records. No filesystem, network or subprocess use.''',
 'loop_peaks': '''Build a three-file Python project with parser.py parse(text), ledger.py aggregate(records), solution.py solve(text), importing between modules. Input is newline-separated category=integer rows. Strip fields. Ignore blanks, stripped # comments and invalid rows. Valid rows have exactly one equals sign, nonempty category and integer matching ASCII [+-]?[0-9]+. Return each category mapped to {"peak": largest accepted integer, "count": number of accepted rows}. Negative peaks must remain negative; repeats count individually. Empty input returns {}. Use loops over parsed records. No filesystem, network or subprocess use.''',
 'tag_manifest': '''Build a three-file Python project: parser.py parses records, ledger.py groups data, solution.py exports solve(text).
The argument text contains newline-separated group:label records. Strip whitespace around each field. Ignore blank lines, lines starting # after stripping, and malformed lines. A valid line contains exactly ONE colon and both stripped fields are nonempty. Labels and groups are case-sensitive strings.
Return a dictionary mapping each group to a sorted list of its DISTINCT labels. Ignore repeated identical group/label pairs. Empty input returns {}. Use imports between all three files. No filesystem, network or subprocess use.''',
 'alert_digest': '''Build a three-file Python project: parser.py parses records, ledger.py groups data, solution.py exports solve(text).
The argument text contains newline-separated severity|message records. Strip whitespace around each field. Ignore blank lines, lines starting # after stripping, and malformed lines. A valid line has exactly ONE vertical bar, severity is exactly WARN or ERROR (case-sensitive), and message is nonempty after stripping. Ignore other severities.
Return {"warnings": sorted distinct WARN messages, "errors": sorted distinct ERROR messages}. A message in both severities appears in both lists. Empty input returns both empty lists. Use imports between all three files. No filesystem, network or subprocess use.''',
 'event_ledger': '''Build a three-file Python project: parser.py handles parsing, ledger.py handles state, solution.py exports solve(text).
Input is newline-separated records id|account|delta. Strip whitespace around fields. Ignore blank lines and lines whose stripped form starts with #.
Valid records have exactly three nonempty fields; delta must match [+-]?[0-9]+ (ASCII digits). Invalid records increment rejected and have no other effect.
Accept each valid id only once across all accounts: first valid occurrence wins. Later valid occurrences increment duplicates, regardless of their payload. An invalid occurrence does not reserve an id.
Return {"balances": {account: integer total}, "rejected": integer, "duplicates": integer}. Include accounts with total zero. No filesystem/network/subprocess use. Use imports between the three modules. Empty input gives empty balances and zero counters.''',
 'revision_ledger': '''Build a three-file Python project: parser.py handles parsing, ledger.py handles state, solution.py exports solve(text).
Input is newline-separated key|revision|value. Strip whitespace around fields. Ignore blanks and stripped lines starting #.
Valid rows have exactly three nonempty fields. Revision matches [0-9]+ (ASCII) and value matches [+-]?[0-9]+ (ASCII). Invalid rows increment rejected and have no effect.
For each key retain the value with the highest numeric revision. If a revision ties the highest seen for that key, FIRST wins. Lower or tied revisions increment stale. A new higher revision replaces the old value; it is not a sum. A stale row never changes retained state.
Return {"values": {key: retained integer value}, "rejected": integer, "stale": integer}. Empty input gives empty values and zero counters. No filesystem/network/subprocess use. Use imports between modules.'''
}


def expected(task,text):
    import re
    if task in ('word_totals','word_peaks'):
        groups={};sep=':' if task=='word_totals' else '='
        for line in text.splitlines():
            if not line.strip() or line.strip().startswith('#'):continue
            parts=[p.strip() for p in line.split(sep)]
            if len(parts)!=2 or not all(parts):continue
            groups.setdefault(parts[0],[]).append(len(parts[1]))
        return {k:({'characters':sum(v),'count':len(v)} if task=='word_totals' else {'longest':max(v),'count':len(v)}) for k,v in groups.items()}
    if task in ('loop_totals','loop_peaks'):
        groups={};sep=':' if task=='loop_totals' else '='
        for line in text.splitlines():
            row=line.strip()
            if not row or row.startswith('#'):continue
            parts=[p.strip() for p in row.split(sep)]
            if len(parts)!=2 or not parts[0] or not re.fullmatch(r'[+-]?[0-9]+',parts[1]):continue
            key,value=parts[0],int(parts[1]);groups.setdefault(key,[]).append(value)
        return {k:({'total':sum(v),'count':len(v)} if task=='loop_totals' else {'peak':max(v),'count':len(v)}) for k,v in groups.items()}
    if task in ('tag_manifest','alert_digest'):
        groups={} if task=='tag_manifest' else {'WARN':set(),'ERROR':set()}
        separator=':' if task=='tag_manifest' else '|'
        for line in text.splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            fields=[f.strip() for f in line.split(separator)]
            if len(fields)!=2 or not all(fields):continue
            group,label=fields
            if task=='alert_digest' and group not in groups:continue
            groups.setdefault(group,set()).add(label)
        if task=='tag_manifest':return {k:sorted(v) for k,v in groups.items()}
        return {'warnings':sorted(groups['WARN']),'errors':sorted(groups['ERROR'])}
    values={};seen={};rejected=duplicates=0
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        fields=[p.strip() for p in line.split('|')]
        valid=len(fields)==3 and all(fields)
        if valid:
            if task=='event_ledger':valid=bool(re.fullmatch(r'[+-]?[0-9]+',fields[2]))
            else:valid=bool(re.fullmatch(r'[0-9]+',fields[1]) and re.fullmatch(r'[+-]?[0-9]+',fields[2]))
        if not valid:rejected+=1;continue
        key,middle,last=fields;value=int(last)
        if task=='event_ledger':
            if key in seen:duplicates+=1;continue
            seen[key]=True;values[middle]=values.get(middle,0)+value
        else:
            revision=int(middle)
            if key in seen and revision<=seen[key]:duplicates+=1;continue
            seen[key]=revision;values[key]=value
    return {('balances' if task=='event_ledger' else 'values'):values,'rejected':rejected,
            ('duplicates' if task=='event_ledger' else 'stale'):duplicates}


def cases(task,phase):
    if task not in TASKS:raise ValueError('Unknown project task')
    if task in ('word_totals','word_peaks'):
        sep=':' if task=='word_totals' else '='
        if phase=='public':
            rows=['','#comment\n ','a:hi\na:there\na:hi\nb:x',' a : two words ','bad','a:b:c',':word','key:','x:caf\u00e9\nx:\u0661']
            rows=[r.replace(':',sep) for r in rows]
        else:
            rng=random.Random(193572 if phase=='heldout' else 654803)
            rows=['\n'.join(rng.choice(['a'+sep+rng.choice(['hi','HELLO','two words','caf\u00e9']), ' b '+sep+' x ', 'bad', 'empty'+sep, '#note']) for _ in range(rng.randint(1,25))) for _ in range(18)]
        return [{'input':text,'expected':expected(task,text)} for text in rows]
    if task in ('loop_totals','loop_peaks'):
        sep=':' if task=='loop_totals' else '='
        if phase=='public':
            rows=['','# note\n \n','a:2\na:-2\na:2\nb:-9',' a : +03 ','invalid','a:2:3',':1','a:١','a:1.5']
            rows=[s.replace(':',sep) for s in rows]
        else:
            rng=random.Random(120309 if phase=='heldout' else 981234)
            rows=['\n'.join(rng.choice([' A '+sep+str(rng.randint(-90,90)), 'b'+sep+'+2', 'bad', 'x'+sep+'１２', '#note']) for _ in range(rng.randint(1,25))) for _ in range(18)]
        return [{'input':text,'expected':expected(task,text)} for text in rows]
    if task in ('tag_manifest','alert_digest'):
        if phase=='public':
            rows=['','# comment\n \n','a:x\na:y\na:x\nb:z',' a : zebra \na:apple\nb:Apple\nb:apple\n:bad\nwrong\na:x:y'] if task=='tag_manifest' else ['', '# comment\n \n','WARN|x\nERROR|x\nWARN|x\nINFO|y',' WARN | zebra \nWARN|apple\nERROR|Apple\nERROR|apple\nERROR|\nwrong\nWARN|x|y']
            # Isolated diagnostic cases make separate contract violations visible;
            # the original combined cases and all private cases remain unchanged.
            rows+=['wrong','a:x:y',' a : zebra ',':bad','a:'] if task=='tag_manifest' else ['wrong','WARN|x|y',' WARN | zebra ','ERROR|','warn|lowercase']
        else:
            rng=random.Random(674321 if phase=='heldout' else 493827);rows=[]
            for _ in range(18):
                lines=['# generated private cases']
                for _ in range(rng.randint(2,15)):
                    group=rng.choice(['a','b','A','c']) if task=='tag_manifest' else rng.choice(['WARN','ERROR','INFO','warn'])
                    separator=':' if task=='tag_manifest' else '|'
                    lines.append(f' {group} {separator} {rng.choice(["alpha","Alpha","zebra","beta","a b"])} ' if rng.random()>.2 else 'malformed')
                rows.append('\n'.join(lines))
        return [{'input':text,'expected':expected(task,text)} for text in rows]
    if phase=='public':
        rows=['', '# comment\n \n',
            'a|cash|3\nb|cash|-3\na|other|999' if task=='event_ledger' else 'a|2|3\na|1|9\na|2|8\na|3|-4',
            'bad\nx|cash|oops\nx|cash|+7\nq|cash|1.0\nempty||2' if task=='event_ledger' else 'bad\nx|-1|5\nx|00|+7\ny|1|1.0\nempty||2',
            ' a | cash | -2 \nb|bank|+4\nc|cash|2\nd|bank|١' if task=='event_ledger' else ' a | 10 | -2 \na|9|4\nb|0|0\nc|١|5']
    else:
        rng=random.Random(782349 if phase=='heldout' else 197452)
        rows=[]
        for _ in range(18):
            lines=['# private generated cases']
            for _ in range(rng.randint(3,15)):
                if rng.random()<.2:lines.append(rng.choice(['wrong','x||1','a|b|0|x','x|1|++1']));continue
                key='k'+str(rng.randrange(5));middle=('acct'+str(rng.randrange(3)) if task=='event_ledger' else str(rng.randrange(12)))
                lines.append(f' {key} | {middle} | {rng.randint(-100,100):+d} ')
            rows.append('\n'.join(lines))
    return [{'input':text,'expected':expected(task,text)} for text in rows]
