"""验证服务字段语义、显式缓存请求和未知统计。"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from anthropic.types import Usage
from conftest import async_test, collect
from mewcode.agent import _total_usage
from mewcode.app import _Renderer
from mewcode.collector import StreamCollector
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.openai import OpenAIProvider
from mewcode.types import Message, ProviderEvent, TokenUsage
from test_anthropic_provider import config, deepseek_config, event, fake_client
from test_openai_provider import chunk, config as ocfg, fake_client as oclient


async def anthropic_sample(cfg, usage):
    client=fake_client([event('message_start',message=NS(usage=usage)),
                       event('content_block_delta',delta=event('text_delta',text='完成')),
                       event('message_delta',delta=NS(stop_reason='end_turn'),usage=NS(output_tokens=9)),event('message_stop')])
    received=await collect(AnthropicProvider(cfg,client=client).stream([Message('user','问')],system_prompt='稳定规则'))
    return [e.usage for e in received if e.usage][-1],client.messages.request


@async_test
async def test_official_anthropic_sums_disjoint_input_and_marks_only_stable_system():
    u,r=await anthropic_sample(config(),NS(input_tokens=100,cache_read_input_tokens=800,cache_creation_input_tokens=200))
    assert (u.input_tokens,u.total_input_tokens,u.cache_read_tokens,u.cache_miss_tokens,u.cache_write_tokens)==(100,1100,800,300,200)
    assert u.cache_complete and u.cache_hit_rate==800/1100
    assert r['system']==[{'type':'text','text':'稳定规则','cache_control':{'type':'ephemeral'}}]
    assert 'cache_control' not in str(r['messages'])


@async_test
async def test_real_deepseek_compatible_sample_is_not_total_and_creation_placeholder_is_unknown():
    u,r=await anthropic_sample(deepseek_config(thinking=True),NS(input_tokens=90,output_tokens=0,cache_read_input_tokens=1024,cache_creation_input_tokens=0,service_tier='standard'))
    assert u.total_input_tokens==1114 and u.cache_miss_tokens==90
    assert u.cache_read_tokens==1024 and u.cache_write_tokens is None and u.cache_complete
    assert r['system']=='稳定规则'


@async_test
async def test_deepseek_explicit_aliases_are_authoritative_not_double_counted():
    u,_=await anthropic_sample(deepseek_config(thinking=False),NS(input_tokens=200,cache_read_input_tokens=800,cache_creation_input_tokens=0,prompt_cache_hit_tokens=800,prompt_cache_miss_tokens=200))
    assert u.total_input_tokens==1000 and u.cache_read_tokens==800 and u.cache_miss_tokens==200
    assert u.cache_write_tokens is None


@pytest.mark.parametrize('url',['https://third-party.test','https://api.anthropic.com.evil.test','http://api.anthropic.com','https://api.anthropic.com/proxy'])
@async_test
async def test_unknown_anthropic_address_gets_no_cache_extension_or_assumed_total(url):
    u,r=await anthropic_sample(replace(config(),base_url=url),NS(input_tokens=100,cache_read_input_tokens=10))
    assert r['system']=='稳定规则'
    assert u.total_input_tokens is None and not u.cache_complete
    assert u.cache_read_tokens==10


@async_test
async def test_sdk_absent_defaults_and_missing_claude_component_stay_unknown():
    source=Usage.model_construct(input_tokens=100,output_tokens=0)
    u,_=await anthropic_sample(config(),source)
    assert u.cache_read_tokens is None and u.cache_write_tokens is None
    assert u.total_input_tokens is None and u.complete and not u.cache_complete


@pytest.mark.parametrize('extra,total,hit,miss,valid',[
    ({'prompt_tokens_details':NS(cached_tokens=800)},1000,800,200,True),
    ({'prompt_cache_hit_tokens':800,'prompt_cache_miss_tokens':200,'prompt_tokens_details':NS(cached_tokens=800)},1000,800,200,True),
    ({},1000,None,None,False),
    ({'prompt_tokens_details':NS(cached_tokens=1200)},1000,1200,None,False),
    ({'prompt_tokens_details':NS(cached_tokens=-1)},1000,None,None,False),
    ({'prompt_tokens_details':NS(cached_tokens=True)},1000,None,None,False),
])
@async_test
async def test_openai_cache_mapping_and_invalid_fields(extra,total,hit,miss,valid):
    client=oclient([chunk('完成'),chunk(finish_reason='stop'),NS(choices=[],usage=NS(prompt_tokens=1000,completion_tokens=9,**extra))])
    events=await collect(OpenAIProvider(ocfg(),client=client).stream([Message('user','问')]))
    u=[e.usage for e in events if e.usage][-1]
    assert (u.total_input_tokens,u.cache_read_tokens,u.cache_miss_tokens,u.cache_complete)==(total,hit,miss,valid)
    assert u.cache_write_tokens is None
    assert (u.cache_hit_rate is not None)==valid
    assert 'cache_control' not in str(client.chat.completions.request)


@async_test
async def test_collector_keeps_latest_counts_and_weighted_total_tracks_each_missing_field():
    first=TokenUsage(1000,9,True,400,None,1000,600,True)
    last=replace(first,cache_read_tokens=800,cache_miss_tokens=200)
    async def stream():
        yield ProviderEvent('usage',usage=first)
        yield ProviderEvent('usage',usage=last)
        yield ProviderEvent('completed',message=Message('assistant','完成'))
    c=StreamCollector(stream())
    await collect(c.events(run_id='x',iteration=1,mode='execute'))
    assert c.usage==last
    second=TokenUsage(100,1,True,0,0,100,100,True)
    total=_total_usage([c.usage,second])
    assert total.cache_hit_rate==800/1100 and total.cache_read_tokens==800
    assert 'cache_write_tokens' in total.incomplete_fields
    partial=_total_usage([c.usage,TokenUsage(100,1,True)])
    assert partial.cache_hit_rate is None and not partial.cache_complete
    assert partial.total_input_tokens==1000 and 'total_input_tokens' in partial.incomplete_fields


def test_renderer_distinguishes_unknown_partial_and_zero_without_claiming_full_ratio():
    known=TokenUsage(200,9,True,800,None,1000,200,True)
    text=_Renderer.usage_text(known)
    assert '总输入 1000' in text and '80.0%' in text and '写入 未知' in text
    text=_Renderer.usage_text(_total_usage([known,TokenUsage(10)]))
    assert '80.0%' not in text and '命中率 未知' in text and '部分' in text
    assert '总输入 未知' in _Renderer.usage_text(TokenUsage(100))
    assert replace(known,total_input_tokens=0,cache_read_tokens=0,cache_miss_tokens=0).cache_hit_rate is None


@async_test
async def test_anthropic_known_miss_does_not_require_unknown_hit():
    u,_=await anthropic_sample(config(),NS(input_tokens=100,cache_creation_input_tokens=200))
    assert u.cache_miss_tokens == 300
    assert u.total_input_tokens is None and u.cache_read_tokens is None and not u.cache_complete


@pytest.mark.parametrize('extra,hit,miss', [({'prompt_cache_hit_tokens':800},800,200),
                                         ({'prompt_cache_miss_tokens':200},800,200)])
@async_test
async def test_openai_partial_explicit_cache_fields_preserve_known_data(extra,hit,miss):
    client=oclient([chunk('完成'),chunk(finish_reason='stop'),NS(choices=[],usage=NS(prompt_tokens=1000,completion_tokens=9,**extra))])
    events=await collect(OpenAIProvider(ocfg(),client=client).stream([]))
    u=[e.usage for e in events if e.usage][-1]
    assert (u.total_input_tokens,u.cache_read_tokens,u.cache_miss_tokens)==(1000,hit,miss)
    assert u.cache_complete


@async_test
async def test_openai_contradictory_total_and_aliases_never_claim_complete_ratio():
    client=oclient([chunk('完成'),chunk(finish_reason='stop'),NS(choices=[],usage=NS(prompt_tokens=500,completion_tokens=9,prompt_cache_hit_tokens=800,prompt_cache_miss_tokens=200))])
    events=await collect(OpenAIProvider(ocfg(),client=client).stream([]))
    u=[e.usage for e in events if e.usage][-1]
    assert not u.cache_complete and u.cache_hit_rate is None
    assert u.complete and events[-1].message.content=='完成'
