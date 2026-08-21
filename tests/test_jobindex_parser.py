from scanner import parse_jobindex


def test_parses_stash_search_response():
    html = '''<html><script>var Stash = {"x":{"searchResponse":{"hitcount":1,"results":[{"tid":"h123","headline":"Head of Sustainability","company":{"name":"Atea Danmark"},"area":"Ballerup","firstdate":"2026-08-21","apply_deadline":"2026-09-01T23:59:00+02:00"}]}}};</script></html>'''
    total, jobs = parse_jobindex(html, "sustainability")
    assert total == 1
    assert len(jobs) == 1
    assert jobs[0].source_id == "h123"
    assert jobs[0].title == "Head of Sustainability"
    assert jobs[0].company == "Atea Danmark"
    assert jobs[0].deadline == "2026-09-01"
