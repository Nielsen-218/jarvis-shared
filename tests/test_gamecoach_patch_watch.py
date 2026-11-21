"""Official EA patch discovery: provenance, same-host safety, source vs fact."""
import pytest
from core import gamecoach_patch_watch as w
from core.gamecoach_upgrades import upgrades,upgrade_summary
from core.gamecoach_meta import LEGENDS

NEWS1="https://www.ea.com/games/apex-legends/apex-legends/news/marked-midseason-patch-notes"
NEWS2="https://www.ea.com/games/apex-legends/apex-legends/news/season-next-patch-notes"
HOTFIX="https://forums.ea.com/blog/apex-legends-game-info-hub-en/axle-hotfix/12345"
HTML='<html><a href="/games/apex-legends/apex-legends/news/marked-midseason-patch-notes">Marked Midseason Patch Notes</a><a href="/games/apex-legends/apex-legends/news/season-next-patch-notes">Season 31 Patch Notes</a><a href="https://evil.example/hack">New Apex Patch</a></html>'
FORUM='<a href="/blog/apex-legends-game-info-hub-en/axle-hotfix/12345">Oct 2026 Hotfix Axle and Alter</a>'
ARTICLE='<h1>Marked Midseason Patch</h1><h2>Fuse</h2><p>Fuse: New Big Bang upgrade expands Knuckle Cluster explosions 50 percent.</p><h3>Catalyst</h3><p>Catalyst can now build stronger door defenses after this update.</p>'

@pytest.fixture
def mem(monkeypatch):
    db={}
    monkeypatch.setattr(w.storage,"load_global",lambda key,default:db.get(key,default))
    monkeypatch.setattr(w.storage,"save_global",lambda key,value:db.__setitem__(key,value))
    return db

def docs(url):
    if url==w.FEEDS["ea_news"]:return HTML
    if url==w.FEEDS["ea_hotfix"]:return FORUM
    if url==NEWS1:return ARTICLE
    if url==NEWS2:return ARTICLE
    if url==HOTFIX:return ARTICLE
    raise ValueError("No fixture for untrusted host")

def test_approved_domains_and_no_url_traversal():
    assert w.canonical(NEWS1)==NEWS1
    assert w.canonical(HOTFIX)==HOTFIX
    for url in ("http://www.ea.com/games/apex-legends/apex-legends/news/other",
                "https://www.ea.com/other",
                "https://www.ea.com.evil.invalid/games/apex-legends/apex-legends/news/x",
                "https://www.ea.com@games.example/other",
                "https://www.ea.com/games/apex-legends/apex-legends/news/../private",
                "https://www.ea.com/games/apex-legends/apex-legends/news/%2e%2e/private",
                "https://127.0.0.1/",
                "https://forums.ea.com:444/x"):
        with pytest.raises(ValueError):w.canonical(url)
    with pytest.raises(ValueError):
        w.canonical(w.FEEDS["ea_hotfix"],must_be_article=True)
    with pytest.raises(ValueError):
        w.canonical(w.FEEDS["ea_news"],must_be_article=True)

def test_discovery_handles_relative_links_and_discards_external():
    out=w.discover(HTML,w.FEEDS["ea_news"])
    assert [x["url"] for x in out]==[NEWS1,NEWS2]
    assert all(x["status"]=="discovered_unreviewed" for x in out)
    out2=w.discover(FORUM,w.FEEDS["ea_hotfix"])
    assert [x["url"] for x in out2]==[HOTFIX]

def test_scan_once_not_double_count_and_each_feed_provenance(mem):
    first=w.scan(download=docs,is_gaming=lambda:False)
    assert first["status"]=="checked"
    assert first["new"]==3
    assert first["pending"]==3
    assert first["successful_feeds"]==2
    again=w.scan(download=docs,is_gaming=lambda:False)
    assert again["new"]==0 and again["pending"]==3
    assert w.report()["last_checked"]
    assert not w.report()["studied"]

def test_legacy_seen_but_unprocessed_link_can_become_patch_relevant(mem):
    mem[w.STORE]={**w._blank(),'seen':[NEWS1]}
    result=w.scan(download=docs,is_gaming=lambda:False)
    assert result['new']==3
    assert NEWS1 in {x['url'] for x in w.state()['pending']}

def test_never_scan_or_study_while_apex_running(mem):
    def no(url):pytest.fail("network requested during gameplay")
    assert w.scan(download=no,is_gaming=lambda:True)["status"]=="deferred_game_running"
    assert w.study_next(download=no,is_gaming=lambda:True)["status"]=="deferred_game_running"
    assert w.state()["checked_at"] is None

def test_only_article_excerpts_not_false_patch_verification(mem):
    w.scan(download=docs,is_gaming=lambda:False)
    out=w.study_next(download=docs,is_gaming=lambda:False)
    assert out["status"]=="indexed_official_unreviewed"
    report=w.report()
    assert report["studied"]==1
    row=w.state()["studied"][0]
    assert row["status"]=="indexed_official_unreviewed"
    assert row["legend_mentions"]
    assert any("Fuse" in r["text"] for r in row["legend_mentions"])
    assert row["url"]==NEWS1
    assert len(w.state()["pending"])==2
    assert "not automatic" in report["meaning"]

def test_feed_outage_retains_queue_and_no_false_success(mem):
    def fail(url):raise OSError("offline")
    a=w.scan(download=fail,is_gaming=lambda:False)
    assert a["status"]=="all_feeds_unavailable"
    assert a["new"]==0
    assert all(f["status"]=="unavailable" for f in w.report()["feeds"].values())
    w.scan(download=docs,is_gaming=lambda:False)
    assert w.report()["pending"]==3
    out=w.study_next(download=fail,is_gaming=lambda:False)
    assert out["status"]=="article_unavailable"
    assert w.report()["pending"]==3

def test_empty_dynamic_html_is_reported_as_no_links(mem):
    result=w.scan(download=lambda _: "<html><script>Javascript app</script></html>",
                  is_gaming=lambda:False)
    assert result["status"]=="all_feeds_unavailable"
    assert all(v["status"]=="no_matching_html_links" for v in w.report()["feeds"].values())

def test_all_28_abilities_plus_only_patch_confirmed_evo_details():
    assert len(LEGENDS)==28
    assert len([name for name in LEGENDS if upgrades(name)["choices"]])==9
    fuse=upgrades("Fuse")
    assert {x["name"] for x in fuse["choices"]}=={"Pyro Techniques","Reignition","Big Bang","Sticky Bomb"}
    assert {x["level"] for x in fuse["choices"]}=={2,3}
    assert "Knuckle Jumper" in fuse["retired_in_patch"]
    assert "Jammed Up" in upgrade_summary("Conduit")
    assert "Ferro-Frenzy" in upgrade_summary("Catalyst")
    assert not upgrades("Pathfinder")["choices"]
    assert "not yet verified" in upgrade_summary("Pathfinder")
    assert all(not upgrades(name)["complete_current_tree_verified"] for name in LEGENDS)

def test_new_tools_are_registered():
    from tools import gaming
    from tools.registry import names
    assert "apex_patch_update_status" in names()
    assert "apex_refresh_patch_knowledge" in names()
    assert "Jammed Up" in gaming.apex_legend_intel("Conduit")
    assert "not yet verified" in gaming.apex_legend_intel("Pathfinder")
    from tools.registry import validate
    assert validate('apex_legend_intel',{'legend':'Pathfinder'}) is None
    assert validate('apex_legend_intel',{'legend':'Ash','mode':'wildcard'}) is None

def test_patch_worker_studies_only_successful_scan(monkeypatch):
    from core.scheduler import Scheduler
    calls=[]
    monkeypatch.setattr(w,"scan",lambda:calls.append("scan") or {"status":"checked","pending":1})
    monkeypatch.setattr(w,"study_next",lambda:calls.append("study"))
    Scheduler(lambda _:None,lambda _:None)._apex_patch_watch()
    assert calls==["scan","study"]
    calls.clear()
    monkeypatch.setattr(w,"scan",lambda:calls.append("scan") or {"status":"deferred_game_running"})
    Scheduler(lambda _:None,lambda _:None)._apex_patch_watch()
    assert calls==["scan"]


def test_patch_mentions_are_attributed_not_automatically_promoted(mem):
    w.scan(download=docs,is_gaming=lambda:False)
    w.study_next(download=docs,is_gaming=lambda:False)
    found=w.legend_evidence("Fuse")
    assert found["status"]=="official_excerpts_unverified"
    assert found["excerpts"][0]["source_url"]==NEWS1
    assert "Fuse" in found["excerpts"][0]["excerpt"]
    assert w.legend_evidence("Wraith")["status"]=="no_indexed_patch_evidence"
    from tools.gaming import apex_patch_evidence_for_legend
    answer=apex_patch_evidence_for_legend("Fuse")
    assert "NOT fully verified" in answer
    assert NEWS1 in answer


def test_page_content_versions_are_tracked_without_changing_current_kit(mem):
    w.scan(download=docs,is_gaming=lambda:False)
    first=w.study_next(download=docs,is_gaming=lambda:False)
    assert first["revision_count"]==1
    # Finish the initial backlog of separate articles; the next study
    # re-fetches the most recent one instead of manufacturing a new link.
    w.study_next(download=docs,is_gaming=lambda:False)
    w.study_next(download=docs,is_gaming=lambda:False)
    same=w.study_next(download=docs,is_gaming=lambda:False)
    assert same["status"]=="unchanged_article"
    original=docs
    def revised(url):
        body=original(url)
        if url==HOTFIX:
            return body.replace("stronger door defenses","adjusted new spike defenses")
        return body
    # All stored pages are revisited, rather than the last page forever.
    outcomes=[w.study_next(download=revised,is_gaming=lambda:False) for _ in range(3)]
    changed=next(x for x in outcomes if x["status"]=="updated_official_unreviewed")
    assert changed["status"]=="updated_official_unreviewed"
    assert changed["revision_count"]==2
    assert w.state()["studied"][-1]["revision_count"]==2
    from core.gamecoach_meta import describe
    assert "Dark Veil" in describe("Catalyst")["ultimate"]

def test_nested_bullets_keep_short_legend_heading_and_ability_context():
    parser=w.TextReader()
    parser.feed('<h1>Patch</h1><p>October 26, 2026</p><h2>Fuse</h2>'
                '<p>Tactical</p><ul><li>Cooldown reduced<ul><li>Now 12 seconds</li></ul></li></ul>'
                '<h2>Weapons</h2><li>Unrelated weapon change</li>')
    mentions,changes=w.article_evidence(parser)
    assert any(x['legend']=='Fuse' and x['section']=='tactical' and '12 seconds' in x['text'] for x in changes)
    assert not any('Unrelated weapon' in x['text'] for x in changes)
    assert w.published_date('October 26, 2026')=='2026-10-26'
    assert w.published_date('Updated yesterday') is None

@pytest.mark.parametrize('stamp,expected',[
    ('15/10/2026','2026-10-15'),('07/10/2026','2026-10-07'),
    ('8/10/2026','2026-10-08'),('01/10/26','2026-10-01'),
    ('09/30/2026','2026-09-30'),('31/02/26',None),
    ('10 days ago',None),('15102026',None),
])
def test_staff_explicit_title_dates_only(stamp,expected):
    assert w.published_date('Latest Update - '+stamp,staff_title=True)==expected
    assert w.published_date(stamp) is None

def test_actual_staff_title_precedes_profile_join_date(mem):
    # Title and account-join structure checked on the public EA staff article:
    # https://forums.ea.com/blog/apex-legends-game-info-hub-en/apex-legends-latest-update---15102026/13803127
    title='Apex Legends: Latest Update - 15/10/2026'
    page='<h1>'+title+'</h1><p>10 days ago</p><p>Joined July 02, 2015</p>'
    mem[w.STORE]={**w._blank(),'pending':[{'url':HOTFIX,'title':title}]}
    assert w.study_next(download=lambda _:page,is_gaming=lambda:False)['status']=='indexed_official_unreviewed'
    assert w.state()['studied'][0]['published_date']=='2026-10-15'
    parser=w.TextReader()
    parser.feed(page.replace(title,'An undated staff update'))
    assert w.source_date('An undated staff update',HOTFIX,parser) is None

def test_staff_hotfix_queue_newest_first_without_promoting_relative_age(mem):
    titles=['Latest Update - 01/10/26','Latest Update - 15/10/2026','Update 2 days ago']
    forum=''.join('<a href="/blog/apex-legends-game-info-hub-en/update/'+str(i)+'">'+title+'</a>'
                  for i,title in enumerate(titles))
    w.scan(download=lambda url:forum if url==w.FEEDS['ea_hotfix'] else HTML,is_gaming=lambda:False)
    assert [x['title'] for x in w.state()['pending'][:2]]==[titles[1],titles[0]]

def test_staff_heading_date_precedes_changed_feed_title():
    parser=w.TextReader()
    parser.feed('<h1>Latest Update - 8/10/2026</h1>')
    assert w.source_date('Latest Update - 07/10/2026',HOTFIX,parser)=='2026-10-08'

def test_content_hash_ignores_script_tracking_but_keeps_revisions(mem):
    w.scan(download=docs,is_gaming=lambda:False)
    for _ in range(3):w.study_next(download=docs,is_gaming=lambda:False)
    same=w.study_next(download=lambda url:docs(url)+'<script>tracking=changed</script>',is_gaming=lambda:False)
    assert same['status']=='unchanged_article'
    changed=[]
    for _ in range(3):
        changed.append(w.study_next(download=lambda url:docs(url).replace('stronger door defenses','different door defenses'),is_gaming=lambda:False))
    assert any(x['status']=='updated_official_unreviewed' for x in changed)
    revised=[x for x in w.state()['studied'] if x['revision_count']>1]
    assert revised and all(x['history'][0]['article_sha256']!=x['article_sha256'] for x in revised)

def test_failed_article_does_not_starve_remaining_queue(mem):
    w.scan(download=docs,is_gaming=lambda:False)
    first=w.state()['pending'][0]['url']
    out=w.study_next(download=lambda _:(_ for _ in ()).throw(OSError('offline')),is_gaming=lambda:False)
    assert out['status']=='article_unavailable'
    assert w.state()['pending'][0]['url']!=first

def test_feed_partial_failure_and_future_season_never_claim_current_build(mem):
    def sources(url):
        if url==w.FEEDS['ea_hotfix']:raise OSError('unavailable')
        if url==NEWS2:return '<h1>Season 31 Patch Notes</h1><p>November 3, 2026</p><p>Future patch.</p>'
        return docs(url)
    w.scan(download=sources,is_gaming=lambda:False)
    w.study_next(download=sources,is_gaming=lambda:False)
    w.study_next(download=sources,is_gaming=lambda:False)
    assert w.report()['season_transition_detected']
    assert not w.report()['coverage_complete']
    assert not w.report()['current_build_verified']

def test_game_started_after_preflight_stops_before_fetch(mem):
    calls=iter([False,True])
    result=w.scan(download=lambda _:pytest.fail('No network during gaming'),is_gaming=lambda:next(calls))
    assert result['status']=='deferred_game_running'

def test_all_existing_legend_profiles_have_explicit_mode_unknowns_and_counterplay():
    from core.gamecoach_legend_details import details
    for name in LEGENDS:
        p=details(name)
        assert p['passive'] and p['tactical'] and p['ultimate']
        assert p['class_perks']['applicable'] and p['counterplay']['advice']
        assert p['unknowns'] and not p['current_build_verified']
        wild=details(name,'wildcard')
        assert not wild['class_perks']['applicable'] and not wild['evo']['applicable']
        assert not wild['evo']['choices']
    assert details('Gibraltar')['additional_passives']
    assert details('Pathfinder')['evo']['unknown_levels']==[2,3]

def test_older_official_help_conflicts_are_exposed_instead_of_overriding_patch():
    from core.gamecoach_legend_details import details
    assert any('immunity' in x for x in details('Catalyst')['conflicts'])
    assert details('Ash')['class']=='skirmisher'
    assert 'removed' in details('Ballistic')['class_perks']['summary']

def test_deferred_game_check_retries_after_match_instead_of_waiting_a_day(monkeypatch):
    from core.scheduler import Scheduler
    monkeypatch.setattr(w,'scan',lambda:{'status':'deferred_game_running'})
    scheduler=Scheduler(lambda _:None,lambda _:None)
    scheduler._apex_patch_watch()
    assert scheduler._apex_patch_retry>0

def test_apex_recall_survives_other_memory_and_respects_wildcard(mem,monkeypatch):
    from core import intelligence,journal,autolearn
    from tools import memory
    monkeypatch.setattr(memory,'recall',lambda _: 'Unrelated memory '*400)
    monkeypatch.setattr(journal,'search',lambda *a:[])
    monkeypatch.setattr(autolearn,'recall',lambda *a,**k:'Other reading '*200)
    core=intelligence.relevant_memory('Explain Bloodhound abilities and upgrades in Apex')
    assert core.startswith('Dated Apex reference')
    assert "Allfather's Cloak" in core and 'True Predator' in core
    assert len(core)<=4000
    wild=intelligence.relevant_memory('Explain Bloodhound abilities in Wildcard')
    assert 'do not apply in Wildcard' in wild and 'True Predator' not in wild

def test_dated_hotfix_precedence_and_console_pc_scope():
    from core.gamecoach_upgrades import hotfixes
    axle=hotfixes('Axle')
    assert axle[0]['published_date']=='2026-10-01' and '650' in axle[0]['effect']
    assert hotfixes('Alter')[0]['published_date']=='2026-10-07'
    movement=hotfixes('Ash','pc')[0]
    assert movement['platform_scope']=='console' and movement['applies_to_requested_platform'] is False
    assert movement['verification']=='reviewed_official_staff_text_not_game_tested'

def test_new_roster_heading_and_expired_reference_fail_closed():
    parser=w.TextReader();parser.feed('<h2>Legends</h2><h3>Unknown New Legend</h3><p>New ability.</p>')
    assert w.roster_candidates(parser)==['Unknown New Legend']
    from core.gamecoach_legend_details import details
    assert details('Bloodhound',as_of='2026-11-04')['reference_expired']
    assert not details('Bloodhound',as_of='2026-10-26')['reference_expired']
    assert not details('Bloodhound',as_of='2026-11-04')['current_build_verified']

def test_hotfix_evidence_tool_does_not_return_superseded_axle_value_first(mem):
    mem[w.STORE]={**w._blank(),'studied':[{'url':NEWS1,'title':'September patch','status':'indexed_official_unreviewed',
        'published_date':'2026-09-14','legend_mentions':[{'legend':'Axle','text':'Nitro Gate initial speed reduced to 700.'}]}]}
    rows=w.legend_evidence('Axle')['excerpts']
    assert rows[0]['published_date']=='2026-10-01' and '650' in rows[0]['excerpt']
    assert rows[0]['review_status']=='reviewed_official_staff_text_not_game_tested'
    from tools.gaming import apex_patch_evidence_for_legend
    answer=apex_patch_evidence_for_legend('Nitro Gate')
    assert '650' in answer and NEWS1 not in answer

def test_upgrade_focus_uses_exact_ingress_words_without_model_paraphrase(mem,monkeypatch):
    from core import pcbridge,users
    from tools.gaming import apex_legend_intel,apex_patch_evidence_for_legend
    monkeypatch.setattr(users,'current',lambda:'owner')
    with pcbridge.user_message('owner','Explain Bloodhound True Predator upgrade briefly.'):
        answer=apex_patch_evidence_for_legend('Bloodhound',focus='True Predator upgrade')
    assert 'True Predator' in answer and 'briefly interrupts' in answer
    assert 'Grappling Hook' not in answer and 'Hidden Sight' not in answer
    wild=apex_legend_intel('Bloodhound','wildcard','True Predator')
    assert 'do not apply in Wildcard' in wild
    with pcbridge.user_message('owner','What is Nitro Gate initial speed in the October hotfix?'):
        latest=apex_patch_evidence_for_legend('Nitro Gate',focus='Nitro Gate speed')
    assert '650' in latest and '2026-10-01' in latest

@pytest.mark.parametrize('question',[
    'According to the October 1 Apex Axle hotfix, what is Nitro Gate initial speed?',
    'What is Axle Nitro Gate initial launch speed after the October 1 hotfix?',
])
def test_concrete_legend_fact_does_not_offer_watcher_status_as_an_answer(question):
    from core import intelligence
    from tools import gaming,registry
    schemas=registry.schemas()
    fact=[{'role':'user','content':question}]
    names={x['name'] for x in intelligence.select_tools(fact,schemas)}
    assert names=={'apex_legend_intel','apex_patch_evidence_for_legend'}
    status=[{'role':'user','content':'Show Apex patch watch status for Axle'}]
    assert 'apex_patch_update_status' in {x['name'] for x in intelligence.select_tools(status,schemas)}
    action=[{'role':'user','content':'Launch Apex and explain Axle hotfix speed'}]
    assert {x['name'] for x in intelligence.select_tools(action,schemas)} != names

def test_unofficial_reader_import_preserves_unknown_tiers_and_source_build(mem):
    from scripts.gamecoach_import_legend_crosschecks import parse
    text='URL Source: https://apex.source.gs/legends/pathfinder/\n\n## Upgrades\n'
    for name,kind,effect in [('Field Research','Passive','Access Survey Beacons.'),('Ringmaster','Passive','Access Ring Consoles.'),
                             ('Landing Gear','Tactical','Softer landings.'),('Feeling Zippy','Ultimate','A second Zipline charge.')]:
        text+=f'\n### {name}\n\n{kind}\n\n{effect}\n'
    text+='\n## Story\nNo lore imported.\n build 11630615 · updated 2026-10-07'
    row=parse('Pathfinder',text)
    assert len(row['choices'])==4 and all(x['level'] is None for x in row['choices'])
    assert all(x['claimed_build']=='11630615' for x in row['choices'])
    assert not row['complete_current_tree_verified']
    with pytest.raises(ValueError):parse('Bangalore',text)
    with pytest.raises(ValueError):parse('Pathfinder',text.replace('updated 2026-10-07','unknown date'))
    from core.gamecoach_upgrades import CROSSCHECK_STORE,upgrades
    mem[CROSSCHECK_STORE]={'legends':{'Pathfinder':row}}
    data=upgrades('Pathfinder')
    assert not data['choices'] and len(data['unofficial_crosscheck_choices'])==4
    assert not data['complete_current_tree_verified']
    with_ability=text.replace('## Upgrades','## Abilities\n\n### Grappling Hook\n\nTactical\n\nReach a distant position.\n\n## Upgrades')
    parsed=parse('Pathfinder',with_ability)
    assert parsed['abilities'][0]['name']=='Grappling Hook'
    assert parsed['abilities'][0]['verification'].startswith('unofficial')
