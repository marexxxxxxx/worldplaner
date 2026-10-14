def test_create_task_is_local(client):
    response = client.post("/api/tasks", json={"title": "Milch kaufen"})
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "local"
    assert body["status"] == "open"
    assert body["version"] == 1
    assert body["title"] == "Milch kaufen"
    assert body["id"]


def test_create_task_requires_title(client):
    response = client.post("/api/tasks", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_field_is_rejected(client):
    response = client.post("/api/tasks", json={"title": "x", "unexpected": 1})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_filters_by_status(client):
    client.post("/api/tasks", json={"title": "offen"})
    done = client.post("/api/tasks", json={"title": "fertig"}).json()
    client.patch(f"/api/tasks/{done['id']}", json={"status": "done", "base_version": 1})

    open_tasks = client.get("/api/tasks", params={"status": "open"}).json()
    assert [task["title"] for task in open_tasks] == ["offen"]
    done_tasks = client.get("/api/tasks", params={"status": "done"}).json()
    assert [task["title"] for task in done_tasks] == ["fertig"]
    assert len(client.get("/api/tasks", params={"status": "all"}).json()) == 2


def test_limit_is_bounded(client):
    assert client.get("/api/tasks", params={"limit": 500}).status_code == 400
    assert client.get("/api/tasks", params={"limit": 0}).status_code == 400
    assert client.get("/api/tasks", params={"limit": 200}).status_code == 200


def test_patch_with_matching_base_version(client):
    task = client.post("/api/tasks", json={"title": "a"}).json()
    response = client.patch(
        f"/api/tasks/{task['id']}", json={"title": "neu", "base_version": 1}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "neu"
    assert response.json()["version"] == 2


def test_patch_with_stale_base_version_conflicts(client):
    task = client.post("/api/tasks", json={"title": "a"}).json()
    client.patch(f"/api/tasks/{task['id']}", json={"title": "neu", "base_version": 1})
    response = client.patch(
        f"/api/tasks/{task['id']}", json={"title": "x", "base_version": 1}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_patch_without_base_version_is_hermes(client):
    task = client.post("/api/tasks", json={"title": "a"}).json()
    response = client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "hermes"
    assert body["status"] == "done"
    assert body["version"] == 2
    assert body["completed_at"] is not None


def test_patch_unknown_task(client):
    response = client.patch("/api/tasks/fehlt", json={"title": "x", "base_version": 1})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_delete_task(client):
    task = client.post("/api/tasks", json={"title": "a"}).json()
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 404
