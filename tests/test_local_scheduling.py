"""Opt-in scheduler templates are local, correctly quoted and metadata-only."""

import plistlib
import xml.etree.ElementTree as ET

import pytest

from livingmeta.domain import Protocol
from livingmeta.local.scheduling import write_templates
from livingmeta.local.workflow import prepare_workspace
from test_local_workspace import make_pdf


def test_scheduler_templates_preserve_paths_and_never_register(tmp_path):
    papers = tmp_path / 'primary papers'
    papers.mkdir()
    make_pdf(papers / 'synthetic.pdf', ['Synthetic paper'])
    workspace = tmp_path / 'private review'
    prepare_workspace(papers, workspace, Protocol())
    result = write_templates(workspace, 'researcher+test@example.org')
    assert result['registered'] is False and result['model_calls'] == 0
    folder = workspace / 'schedule'
    plist = plistlib.loads((folder / 'monitor.plist').read_bytes())
    assert plist['ProgramArguments'][-3:] == [str(workspace), '--contact-email', 'researcher+test@example.org']
    assert 'monitor-due' in plist['ProgramArguments'] and 'run' not in plist['ProgramArguments']
    assert plist['RunAtLoad'] and plist['StartCalendarInterval'] == {'Minute': 0}
    cron = (folder / 'monitor.cron').read_text()
    assert cron.startswith('0 * * * * ') and "'" + str(workspace) + "'" in cron
    task = ET.parse(folder / 'monitor-task.xml')
    ns = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    assert task.find('.//t:StartWhenAvailable', ns).text == 'true'
    assert 'monitor-due' in task.find('.//t:Arguments', ns).text


@pytest.mark.parametrize('email', ['missing', 'bad\n@example.org', 'bad\0@example.org'])
def test_scheduler_rejects_invalid_email(tmp_path, email):
    papers = tmp_path / 'papers'
    papers.mkdir()
    make_pdf(papers / 'synthetic.pdf', ['Synthetic paper'])
    workspace = tmp_path / 'workspace'
    prepare_workspace(papers, workspace, Protocol())
    with pytest.raises(ValueError):
        write_templates(workspace, email)
    assert not (workspace / 'schedule').exists()
