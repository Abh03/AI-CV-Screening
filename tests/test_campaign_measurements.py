from scripts.summarize_campaign_measurements import distribution, memory_mib, summarize
import pytest


def test_provider_usage_counts_errors_without_fabricating_usage_or_billing():
    report = {"started_at": "2026-09-26T10:00:00+00:00", "campaign_id": "test", "samples": []}
    events = [{"timestamp": "2026-09-26T10:01:00+00:00", "event": "provider_request"},
              {"timestamp": "2026-09-26T10:01:01+00:00", "event": "provider_response",
               "provider": "Groq", "model": "openai/gpt-oss-120b", "status": 200,
               "latency_ms": 1000, "input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
              {"timestamp": "2026-09-26T10:02:00+00:00", "event": "provider_request"},
              {"timestamp": "2026-09-26T10:02:01+00:00", "event": "provider_response",
               "provider": "Groq", "model": "openai/gpt-oss-120b", "status": 429, "latency_ms": 100}]
    result = summarize(report, [], events)["provider"]
    assert result["counts"] == {"requests": 2, "200": 1, "429": 1, "responses_with_usage": 1}
    assert result["tokens"]["total_tokens"] == 1500
    assert result["provider_reported_cost"] is None
    assert result["uncached_list_price_estimate_usd"] == .00045


def test_resource_units_and_distribution_preserve_peak_measurements():
    assert memory_mib("2GiB ") == 2048
    assert memory_mib("512MiB ") == 512
    assert distribution([2, 1, 3])["max"] == 3
    assert distribution([]) is None


def test_finished_campaign_excludes_later_provider_activity():
    report={'started_at':'2026-09-26T10:00:00+00:00','campaign_id':'test','samples':[],
            'status':'COMPLETED','elapsed_seconds':60}
    events=[dict(timestamp='2026-09-26T10:00:30+00:00',event='provider_request'),
            dict(timestamp='2026-09-26T10:01:30+00:00',event='provider_response',
                 status=429,latency_ms=100,provider='Gemini',model='gemini-3.7-flash')]
    assert summarize(report,[],events)['provider']['counts']=={'requests':1}


def test_overlapping_log_collectors_do_not_double_count_provider_or_tasks():
    report = {'started_at':'2026-09-26T10:00:00+00:00','campaign_id':'test','samples':[]}
    events = [dict(timestamp='2026-09-26T10:01:00+00:00',event='provider_request'),
              dict(timestamp='2026-09-26T10:01:01+00:00',event='provider_response',status=200,
                   provider='Groq',model='openai/gpt-oss-120b',latency_ms=1000,total_tokens=1500),
              dict(timestamp='2026-09-26T10:01:02+00:00',event='task_finished',run_id='unique-task',
                   stage='campaign.stage3_pair',task_runtime_ms=1100)]
    result=summarize(report,[],events+events)
    assert result['provider']['counts']['requests']==1
    assert result['provider']['tokens']['total_tokens']==1500
    assert result['tasks']['campaign.stage3_pair']['runtime_ms']['count']==1
    assert result['duplicate_captured_events_ignored']==3


@pytest.mark.parametrize('gemini_model',['gemini-3.6-flash','gemini-3.7-flash'])
def test_mixed_provider_cost_is_partial_when_groq_does_not_report_billing(gemini_model):
    report={'started_at':'2026-09-26T10:00:00+00:00','campaign_id':'test','samples':[]}
    rows=[dict(timestamp='2026-09-26T10:01:00+00:00',event='provider_response',provider='Groq',
               model='openai/gpt-oss-120b',status=200,latency_ms=1000,input_tokens=1000,output_tokens=500,total_tokens=1500),
          dict(timestamp='2026-09-26T10:02:00+00:00',event='provider_response',provider='OpenRouter',
               model='openrouter/free',status=200,latency_ms=2000,input_tokens=1000,output_tokens=500,total_tokens=1500,cost=0),
          dict(timestamp='2026-09-26T10:03:00+00:00',event='provider_response',provider='Gemini',
               model=gemini_model,status=200,latency_ms=2000,input_tokens=1000,output_tokens=500,total_tokens=1500)]
    result=summarize(report,[],rows)['provider']
    assert result['provider_reported_cost'] is None
    assert result['reported_partial_cost_sum']==0
    assert result['uncached_list_price_estimate_usd']==.003075
    assert len(result['by_provider_model'])==3
