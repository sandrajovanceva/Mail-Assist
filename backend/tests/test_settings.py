def test_defaults_are_created_on_first_read(client, auth):
    body = client.get("/settings", headers=auth).json()
    assert body["default_tone"] == "professional"
    assert body["auto_send_enabled"] is False


def payload(**overrides):
    base = {
        "user_name": "Sandra",
        "signature": "Best,\nSandra",
        "default_tone": "friendly",
        "style_profile": "- short sentences",
        "auto_send_enabled": True,
        "auto_send_categories": "routine",
        "auto_send_daily_limit": 3,
    }
    base.update(overrides)
    return base


def test_settings_round_trip(client, auth):
    saved = client.put("/settings", headers=auth, json=payload()).json()
    assert saved["auto_send_enabled"] is True
    assert saved["auto_send_daily_limit"] == 3
    assert client.get("/settings", headers=auth).json() == saved


def test_important_cannot_be_added_to_auto_send(client, auth):
    response = client.put(
        "/settings", headers=auth, json=payload(auto_send_categories="routine,important")
    )
    assert response.status_code == 400
    assert "important" in response.json()["detail"].lower()


def test_unknown_tone_is_rejected(client, auth):
    assert client.put("/settings", headers=auth, json=payload(default_tone="sassy")).status_code == 400


def test_negative_daily_limit_is_rejected(client, auth):
    assert client.put("/settings", headers=auth, json=payload(auto_send_daily_limit=-1)).status_code == 400


def test_knowledge_crud(client, auth):
    created = client.post(
        "/settings/knowledge",
        headers=auth,
        json={"kind": "fact", "title": "Call hours", "content": "Tue-Thu 10-15 CET"},
    ).json()
    assert created["kind"] == "fact"

    assert len(client.get("/settings/knowledge", headers=auth).json()) == 1

    assert client.delete(f"/settings/knowledge/{created['id']}", headers=auth).status_code == 200
    assert client.get("/settings/knowledge", headers=auth).json() == []


def test_knowledge_rejects_unknown_kind(client, auth):
    response = client.post(
        "/settings/knowledge", headers=auth, json={"kind": "poem", "title": "x", "content": "y"}
    )
    assert response.status_code == 400


def test_knowledge_is_private_to_its_owner(client, auth):
    from tests.conftest import register

    client.post(
        "/settings/knowledge",
        headers=auth,
        json={"kind": "fact", "title": "Mine", "content": "secret"},
    )
    other = register(client, email="other@example.com")
    headers = {"Authorization": f"Bearer {other['token']}"}
    assert client.get("/settings/knowledge", headers=headers).json() == []


def test_learn_style_needs_a_mailbox(client, auth):
    response = client.post("/settings/style/learn", headers=auth)
    assert response.status_code == 400
    assert "gmail" in response.json()["detail"].lower()


def test_learn_style_writes_a_profile(client, auth, account, fake_provider, monkeypatch):
    monkeypatch.setattr(
        "app.routers.settings.llm.analyze_style",
        lambda bodies, db=None, user_id=None: "- Greets with 'Hi'\n- Signs off 'Cheers'",
    )
    body = client.post("/settings/style/learn", headers=auth).json()
    assert "Cheers" in body["style_profile"]
