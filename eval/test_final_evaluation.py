"""Check evaluation integrity: errors, pending inputs and frozen data."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).parent))
import final_evaluation as evaluation

def fixture_data(tmp_path,monkeypatch):
    monkeypatch.setattr(evaluation,'OUT',tmp_path)
    rows=[dict(id=str(i),sender='test@example.invalid',subject=f'Case {i}',body='Body',spam_label=i<2,group='spam' if i<2 else 'easy_ham') for i in range(4)]
    dataset=tmp_path/'emails.jsonl'
    dataset.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    evaluation.write_json(tmp_path/'manifest.json',dict(dataset_sha256=evaluation.digest(dataset.read_bytes()),
        classifier_source_sha256=evaluation.digest(Path(evaluation.llm.__file__).read_bytes()),
        classifier_model=evaluation.settings.classifier_model,thinking_level=evaluation.settings.classifier_thinking_level))
    return rows

def predictions(tmp_path,records):
    (tmp_path/'predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')

def test_failures_count_against_primary_accuracy(tmp_path,monkeypatch):
    fixture_data(tmp_path,monkeypatch)
    predictions(tmp_path,[dict(id='0',status='ok',prediction={'category':'spam'}),dict(id='1',status='error',error='empty'),
                          dict(id='2',status='ok',prediction={'category':'routine'}),dict(id='3',status='ok',prediction={'category':'spam'})])
    evaluation.report(quiet=True)
    metrics=json.loads((tmp_path/'metrics.json').read_text())
    assert metrics['complete'] is True
    assert metrics['primary_accuracy']==.5
    assert metrics['valid_accuracy']==pytest.approx(2/3)
    assert metrics['full_recall']==.5
    assert metrics['failed']==1

def test_reliability_and_latency_reporting(tmp_path,monkeypatch):
    fixture_data(tmp_path,monkeypatch)
    predictions(tmp_path,[dict(id='0',status='ok',prediction={'category':'spam'},latency_ms=1000),
                          dict(id='1',status='error',error='classify: Gemini returned an empty response',latency_ms=500),
                          dict(id='2',status='error',error='classify failed: 503 UNAVAILABLE.',latency_ms=700),
                          dict(id='3',status='ok',prediction={'category':'routine'},latency_ms=3000)])
    evaluation.report(quiet=True)
    metrics=json.loads((tmp_path/'metrics.json').read_text())
    assert metrics['failure_rate']==.5
    assert metrics['failure_causes']=={'empty model response':1,'HTTP 503 model overloaded':1}
    # Latency covers valid outputs only.
    assert metrics['latency_ms']['n']==2 and metrics['latency_ms']['median']==2000
    assert metrics['groups']['spam']=={'planned':2,'attempted':2,'valid':1,'correct':1,'valid_accuracy':1.0,'full_accuracy':.5}
    assert metrics['telemetry']=={}

def test_partial_run_has_no_final_primary_score(tmp_path,monkeypatch):
    fixture_data(tmp_path,monkeypatch)
    predictions(tmp_path,[dict(id='0',status='ok',prediction={'category':'spam'})])
    evaluation.report(quiet=True)
    metrics=json.loads((tmp_path/'metrics.json').read_text())
    assert metrics['primary_accuracy'] is None
    assert metrics['complete'] is False
    assert metrics['attempted']==1

def test_dataset_mutation_is_rejected(tmp_path,monkeypatch):
    fixture_data(tmp_path,monkeypatch)
    with (tmp_path/'emails.jsonl').open('a') as handle:handle.write('\n')
    with pytest.raises(AssertionError,match='Dataset changed'):
        evaluation.frozen()

def test_unicode_line_separators_inside_json_strings(tmp_path):
    path=tmp_path/'unicode.jsonl'
    path.write_text(json.dumps({'body':'A\u0085B\u2028C'},ensure_ascii=False)+'\n',encoding='utf-8')
    assert evaluation.read_jsonl(path)==[{'body':'A\u0085B\u2028C'}]
