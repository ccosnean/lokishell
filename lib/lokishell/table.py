"""Small, terminal-width-aware tables without another runtime dependency."""
import shutil
import textwrap

import click


def echo_table(headers, rows):
    cells = [headers, *rows]
    widths = [max(len(str(row[index])) for row in cells) for index in range(len(headers))]
    available = max(sum(len(header) for header in headers),
                    shutil.get_terminal_size((100, 24)).columns - 3 * len(headers) - 1)
    while sum(widths) > available:
        index = max(range(len(widths)), key=lambda i: widths[i] - len(headers[i]))
        widths[index] -= 1
    try:
        '┌─┬┐│├┼┤└┴┘'.encode(click.get_text_stream('stdout').encoding or 'utf-8')
        top, middle, bottom, vertical = ('┌', '┬', '┐'), ('├', '┼', '┤'), ('└', '┴', '┘'), '│'
        horizontal = '─'
    except UnicodeEncodeError:
        top = middle = bottom = ('+', '+', '+')
        vertical, horizontal = '|', '-'

    def border(parts):
        click.echo(parts[0] + parts[1].join(horizontal * (width + 2) for width in widths) + parts[2])

    border(top)
    for index, row in enumerate(cells):
        wrapped = [textwrap.wrap(str(value), width=width, break_on_hyphens=False) or ['']
                   for value, width in zip(row, widths)]
        for line in range(max(map(len, wrapped))):
            values = [column[line] if line < len(column) else '' for column in wrapped]
            click.echo(vertical + vertical.join(' ' + value.ljust(width) + ' '
                                                for value, width in zip(values, widths)) + vertical)
        if index < len(cells) - 1:
            border(middle)
    border(bottom)
