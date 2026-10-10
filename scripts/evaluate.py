"""Run CP5 trials or rebuild summaries without generating additional responses."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import click
from dotenv import load_dotenv

from evaluation import CampaignBudget, ROOT, campaign_usage, load_cases, manifest, run_gameplay, run_trial, summarize
from player import Settings
from run_store import RunStore


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


@click.command()
@click.option('--output-dir', type=click.Path(file_okay=False, path_type=Path))
@click.option('--models', multiple=True, type=click.Choice(['gpt-6-luna', 'gpt-6.1-sol']), help='Repeat this option for each candidate; default: both.')
@click.option('--repetitions', type=click.IntRange(min=1, max=10), default=2, show_default=True)
@click.option('--suite', type=click.Choice(['all', 'fixtures', 'gameplay', 'smoke']), default='all', show_default=True)
@click.option('--request-budget', type=click.IntRange(min=1), default=96, show_default=True)
@click.option('--token-budget', type=click.IntRange(min=1), default=1000000, show_default=True)
@click.option('--cost-budget', type=click.FloatRange(min=0.001), default=2.0, show_default=True, help='Conservative local USD allowance; not a provider billing cap.')
@click.option('--dry-run', is_flag=True, help='Print the protocol without loading credentials or calling models.')
@click.option('--report-only', is_flag=True, help='Rebuild summary.json from an existing output directory; no API calls.')
def cli(output_dir, models, repetitions, suite, request_budget, token_budget, cost_budget, dry_run, report_only):
    if report_only:
        if not output_dir:
            raise click.ClickException('--report-only requires --output-dir.')
        rows = [json.loads(line) for line in (output_dir / 'results.jsonl').read_text(encoding='utf-8').splitlines() if line]
        store = RunStore(output_dir / 'runs.sqlite3')
        try:
            write_json(output_dir / 'summary.json', {'groups': summarize(rows), 'campaign': campaign_usage(store)})
        finally:
            store.close()
        click.echo('Summary rebuilt without API requests.')
        return
    cases = load_cases()
    models = list(models or ('gpt-6-luna', 'gpt-6.1-sol'))
    options = {'models': models, 'repetitions': repetitions, 'suite': suite,
               'request_budget': request_budget, 'token_budget': token_budget, 'cost_budget_usd': cost_budget,
               'reasoning': 'low', 'memory_model': 'gpt-6-luna', 'memory_reasoning': 'low',
               'retries': 0, 'gameplay_steps': 4, 'evaluation_output_tokens': 4096}
    if dry_run:
        click.echo(json.dumps({'options': options, 'cases': [{'id': c['id'], 'turns': len(c['turns']),
                                                             'probes': [p['question'] for p in c['probes']]} for c in cases]}, ensure_ascii=False, indent=2))
        return
    load_dotenv(ROOT / '.env', override=False)
    if not os.getenv('OPENAI_API_KEY', '').strip():
        raise click.ClickException('OPENAI_API_KEY is missing.')
    if output_dir is None:
        output_dir = ROOT / 'data/evaluations' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    if output_dir.exists() and any(output_dir.iterdir()):
        raise click.ClickException('Output directory is not empty; choose a new directory or use --report-only. Existing results are preserved.')
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / 'manifest.json', manifest(cases, options))
    store = RunStore(output_dir / 'runs.sqlite3', request_guard=CampaignBudget(requests=request_budget, tokens=token_budget, usd=cost_budget))
    jobs = []
    if suite in ('all', 'fixtures', 'smoke'):
        selected_cases = cases[-1:] if suite == 'smoke' else cases
        for repetition in range(1 if suite == 'smoke' else repetitions):
            for case in selected_cases:
                for model in models:
                    for mode in ('full-history', 'hybrid'):
                        jobs.append(('fixture', case, model, mode, repetition + 1))
    if suite in ('all', 'gameplay'):
        for part in ('del1', 'del2'):
            for model in models:
                for mode in ('full-history', 'hybrid'):
                    jobs.append(('gameplay', part, model, mode, 1))
    rows = []
    blocked = set()
    try:
        with (output_dir / 'results.jsonl').open('a', encoding='utf-8') as output:
            for number, (kind, case, model, mode, repetition) in enumerate(jobs, 1):
                settings = Settings(model=model, memory_mode=mode, retries=0, timeout=45,
                                    max_output_tokens=4096 if kind == 'fixture' else 2048)
                if model in blocked or ('memory' in blocked and mode == 'hybrid'):
                    click.echo(f'[{number}/{len(jobs)}] {model}/{mode}: skipped after model-access failure.')
                    rows.append({'kind': kind, 'case': case['id'] if kind == 'fixture' else case,
                                 'model': model, 'mode': mode, 'status': 'skipped_model_access',
                                 'probe_count': len(case['probes']) if kind == 'fixture' else 0,
                                 'usage': {'requests': 0, 'known_tokens': 0}, 'estimated_usd': 0})
                else:
                    label = case['id'] if kind == 'fixture' else case
                    click.echo(f'[{number}/{len(jobs)}] {kind} {label} {model}/{mode} repetition {repetition}', nl=True)
                    row = run_trial(store, case, settings, repetition) if kind == 'fixture' else run_gameplay(store, case, settings)
                    rows.append(row)
                    click.echo(f"  {row['status']}; requests={row['usage']['requests']}; tokens={row['usage']['known_tokens']}; probes={sum(s['passed'] for s in row.get('scores', []))}/{row.get('probe_count', 0)}")
                    for attempt in row['attempts']:
                        if attempt.get('http_status') in (403, 404):
                            blocked.add('memory' if attempt['purpose'] == 'memory' else model)
                    if any(a.get('http_status') == 401 for a in row['attempts']) or row.get('error_type') == 'BudgetExceeded':
                        output.write(json.dumps(row, ensure_ascii=False) + '\n')
                        output.flush()
                        click.echo('Campaign stopped: authorization failure or configured allowance exhausted.')
                        break
                output.write(json.dumps(rows[-1], ensure_ascii=False) + '\n')
                output.flush()
    finally:
        write_json(output_dir / 'summary.json', {'groups': summarize(rows), 'campaign': campaign_usage(store)})
        store.close()
    click.echo(f'Results: {output_dir}')


if __name__ == '__main__':
    cli()
