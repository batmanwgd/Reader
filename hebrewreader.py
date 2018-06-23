#!/usr/bin/env python3
from argparse import ArgumentParser, FileType, RawTextHelpFormatter
import os
import re
from shutil import copyfile
import subprocess
import sys
import tempfile
import textwrap

from tf.fabric import Fabric

PASSAGE_RGX = (
    r'^(?P<book>(?:\d )?[a-zA-Z ]+) '
    r'(?P<startchap>\d+)(?::(?P<startverse>\d+))?'
    r'(?:-(?P<endref>(?P<endchap>\d+)(?::(?P<endverse>\d+))?|(?:book)?end))?$'
)

FEATURES = 'g_word_utf8 gloss lex_utf8 otype trailer_utf8 voc_lex_utf8'

def parse_passage(passage):
    match = re.match(PASSAGE_RGX, passage)
    if match is None:
        match = {'book': passage, 'startchap': 1, 'startverse': 1,
                'endchap': None, 'endverse': None, 'endref': 'bookend'}
    else:
        match = match.groupdict()

    match['book'] = match['book'].replace(' ', '_')
    match['startchap'] = int(match['startchap'])
    if match['startverse'] is not None:
        match['startverse'] = int(match['startverse'])
    if match['endchap'] is not None:
        match['endchap'] = int(match['endchap'])
    if match['endverse'] is not None:
        match['endverse'] = int(match['endverse'])

    try:
        if match['endchap'] is None:
            if match['startverse'] is None and match['endref'] is None:
                match['endref'] = 'end'
            else:
                match['endchap'] = match['startchap']
                match['endverse'] = match['startverse']
        elif match['endverse'] is None:
            location = T.nodeFromSection((match['book'], match['endchap'], 1))
            location = L.u(location, otype='chapter')[0]
            location = L.n(location, otype='chapter')[0]
            location = L.p(location, otype='verse')[0]
            _, _, match['endverse'] = T.sectionFromNode(location)

        if match['startverse'] is None:
            match['startverse'] = 1

        if match['endref'] == 'end':
            location = T.nodeFromSection((match['book'], match['startchap']))
            location = L.n(location, otype='chapter')[0]
            location = L.d(location, otype='verse')[0]
            location = L.p(location, otype='verse')[0]
            _, match['endchap'], match['endverse'] = T.sectionFromNode(location)
        elif match['endref'] == 'bookend':
            location = T.nodeFromSection((match['book'],))
            location = L.n(location, otype='book')[0]
            location = L.d(location, otype='verse')[0]
            location = L.p(location, otype='verse')[0]
            _, match['endchap'], match['endverse'] = T.sectionFromNode(location)
    except:
        raise ValueError('Could not find reference "{}"'.format(passage))

    match.pop('endref')
    return match

def verses_in_passage(passage):
    for chap in range(passage['startchap'], passage['endchap']+1):
        start = passage['startverse'] if chap == passage['startchap'] else 1
        if chap == passage['endchap']:
            end = passage['endverse']
        else:
            location = T.nodeFromSection((passage['book'], chap+1))
            location = L.d(location, otype='verse')[0]
            location = L.p(location, otype='verse')[0]
            _, _, end = T.sectionFromNode(location)

        for verse in range(start, end+1):
            yield (passage['book'], chap, verse)

def fix_trailer(trailer):
    return trailer\
            .replace('\n', '')\
            .replace('\u05e1', r'\setuma{}')\
            .replace('\u05e4', r'\petucha{}')

def fix_gloss(gloss):
    if gloss == 'i':
        return 'I'
    return re.sub(r'<(.*)>', r'\\textit{\1}', gloss)

def get_passage_and_words(passage, separate_chapters=True, verse_nos=True):
    text = []
    words = set()

    last_chapter = None
    for verse in verses_in_passage(passage):
        if verse[1] != last_chapter:
            last_chapter = verse[1]
            if separate_chapters:
                text.append('\n')
        node = T.nodeFromSection(verse)
        wordnodes = L.d(node, otype='word')
        thistext = ''
        if verse_nos:
            if verse[2] == 1:
                thistext += r'\rdrchap{%d}' % verse[1]
            thistext += r'\rdrverse{%d} ' % verse[2]
        thiswords = []
        for word in wordnodes:
            thiswords.append(
                    F.g_word_utf8.v(word) +
                    fix_trailer(F.trailer_utf8.v(word)))
            lex = L.u(word, otype='lex')[0]
            words.add((F.lex_utf8.v(word), F.voc_lex_utf8.v(lex), fix_gloss(F.gloss.v(lex))))
        thistext += ''.join(thiswords)
        text.append(thistext)

    return text, sorted(words)

def generate(passages, combine_voca, tex, pdf, templates, quiet=False):
    tex.write(templates['pre'])

    voca = set()

    for passage_text in passages:
        passage = parse_passage(passage_text)

        passage_pretty = '{} {}:{} - {}:{}'.format(
            passage['book'].replace('_', ' '),
            passage['startchap'], passage['startverse'],
            passage['endchap'], passage['endverse'])
        tex.write(r'\def\thepassage{%s}' % passage_pretty)

        try:
            text, words = get_passage_and_words(passage)
        except:
            raise ValueError('Could not find reference "{}"'.format(passage_text))

        tex.write('\n\n' + templates['pretext'])
        tex.write('\n'.join(text))
        tex.write('\n' + templates['posttext'])

        if combine_voca:
            voca.update(words)
        else:
            tex.write('\n\n' + templates['prevoca'])
            tex.write('\\\\\n'.join(r'{\hebrewfont\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in words))
            tex.write('\n' + templates['postvoca'])

    if combine_voca:
        tex.write('\n\n' + templates['prevoca'])
        tex.write('\\\\\n'.join(r'{\hebrewfont\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in sorted(voca)))
        tex.write('\n' + templates['postvoca'])

    tex.write(templates['post'])

    tex.close()

    if pdf is None:
        return (tex.name, None)

    path, filename = os.path.split(pdf)
    jobname, _ = os.path.splitext(filename)

    cmd = ['xelatex']
    if path != '':
        cmd.append('-output-directory')
        cmd.append(path)
    cmd.append('-jobname')
    cmd.append(jobname)
    cmd.append(tex.name)

    if quiet:
        null = open(os.devnull, 'wb')
        subprocess.call(cmd, stdout=null, stderr=null)
    else:
        subprocess.run(cmd)

    return (tex.name, pdf)

def load_data(locations, modules):
    TF = Fabric(locations=locations, modules=modules, silent=True)
    api = TF.load(FEATURES, silent=True)
    api.makeAvailableIn(globals())

def main():
    parser = ArgumentParser(
            description='LaTeX reader generator for Biblical Hebrew',
            formatter_class=RawTextHelpFormatter)

    p_data = parser.add_argument_group('Data source options')
    p_data.add_argument('--bhsa', '-b', nargs=1, required=True,
            help='Location of the BHSA data')
    p_data.add_argument('--module', '-m', nargs=1, required=True,
            help='Text-fabric module to load')

    p_tex = parser.add_argument_group('TeX template options')
    p_tex.add_argument('--pre-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('pre.tex', encoding='utf-8'),
            help='TeX file to prepend to output')
    p_tex.add_argument('--post-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('post.tex', encoding='utf-8'),
            help='TeX file to append to output')
    p_tex.add_argument('--pre-text-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('pretext.tex', encoding='utf-8'),
            help='TeX file to prepend to texts')
    p_tex.add_argument('--post-text-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('posttext.tex', encoding='utf-8'),
            help='TeX file to append to texts')
    p_tex.add_argument('--pre-voca-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('prevoca.tex', encoding='utf-8'),
            help='TeX file to prepend to word list')
    p_tex.add_argument('--post-voca-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('postvoca.tex', encoding='utf-8'),
            help='TeX file to append to word list')

    p_output = parser.add_argument_group('Output options')
    p_output.add_argument('--tex', type=FileType('w', encoding='utf-8'),
            metavar='FILE', help='File to write the TeX code to')
    p_output.add_argument('--pdf',
            metavar='FILE', help='The output PDF file')

    p_misc = parser.add_argument_group('Miscellaneous options')
    p_misc.add_argument('--combine-voca', action='store_true',
            help='Use one vocabulary list for all passages')

    parser.add_argument('passages', metavar='PASSAGE', nargs='*',
            help=textwrap.dedent('''\
            The passages to include.
            Examples of correct input are:\n
            - Psalms 1
            - Exodus 3:15
            - Genesis 1-2:3
            - Genesis 2:4-11 (NB: the 11 is the chapter!)
            - 1 Kings 17:7-end
            - Job 38:1-bookend'''))

    args = parser.parse_args()

    if args.tex is None:
        tex = tempfile.mkstemp(suffix='.tex', prefix='reader')
        args.tex = open(tex[1], 'w', encoding='utf-8')
    if args.pdf is None:
        args.pdf = tempfile.mkstemp(suffix='.pdf', prefix='reader')[1]

    print('Loading data...')
    load_data(args.bhsa, args.module)

    if len(args.passages) == 0:
        print('No passages given, not doing anything')
        return

    print('Generating reader...')
    try:
        templates = {}
        templates['pre'] = args.pre_tex.read()
        templates['post'] = args.post_tex.read()
        templates['pretext'] = args.pre_text_tex.read()
        templates['posttext'] = args.post_text_tex.read()
        templates['prevoca'] = args.pre_voca_tex.read()
        templates['postvoca'] = args.post_voca_tex.read()
        generate(args.passages, args.combine_voca, args.tex, args.pdf, templates)
    except Exception as e:
        print(e)
        sys.exit(1)

    print('Output written to', args.pdf)

if __name__ == '__main__':
    main()