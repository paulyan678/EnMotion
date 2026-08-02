from __future__ import annotations

import time

from src.apps.comic_gen.models import Character, ImageVariant, Script, StoryboardFrame, VideoTask
from src.apps.hybrid import activity


def test_hybrid_asset_activity_is_durable_and_identifies_the_asset(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))

    activity.record_asset_activity(
        "workspace-alice",
        task_id="task-1",
        job_type="series_asset",
        source="workspace",
        source_route="#/series/series-1",
        source_id="series-1",
        series_id="series-1",
        asset_id="asset-1",
        asset_type="character",
        asset_name="守塔人",
        prompt="全身角色设定图",
        model_name="gpt-image-2",
        batch_size=1,
        aspect_ratio="9:16",
    )

    queued = activity.list_activity("workspace-alice")
    assert len(queued) == 1
    assert queued[0]["detail"] == "守塔人"
    assert queued[0]["prompt"] == "全身角色设定图"
    assert queued[0]["status"] == "queued"
    assert queued[0]["source_context"]["asset_id"] == "asset-1"
    assert "_process_id" not in queued[0]
    assert activity.get_activity("workspace-alice", "task-1") == queued[0]
    assert activity.get_activity("workspace-alice", "missing-task") is None

    activity.update_asset_activity("workspace-alice", "task-1", status="running")
    activity.update_asset_activity(
        "workspace-alice",
        "task-1",
        status="completed",
        outputs=[
            {
                "id": "variant-1",
                "media_type": "image",
                "media_path": "assets/variant-1.png",
                "thumbnail_path": "assets/variant-1.png",
                "filename": "variant-1.png",
            }
        ],
    )
    completed = activity.list_activity("workspace-alice")[0]
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["finished_at"]
    assert completed["outputs"][0]["id"] == "variant-1"


def test_hybrid_video_activity_persists_input_parameters_and_output(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    activity.record_video_activity(
        "workspace-alice",
        task_id="video-task-1",
        job_type="video",
        source="workspace",
        source_route="#/project/project-1",
        detail="镜头 1",
        prompt="A lantern sways in the rain",
        model_name="doubao-seedance-2-0-fast-260128",
        duration=5,
        generation_mode="i2v",
        resolution="720p",
        ratio="16:9",
        source_context={
            "project_id": "project-1",
            "frame_id": "frame-1",
            "video_task_id": "video-task-1",
        },
        input_media=[
            {
                "id": "input-1",
                "media_type": "image",
                "media_path": "storyboard/frame-1.png",
            }
        ],
    )
    activity.update_asset_activity(
        "workspace-alice",
        "video-task-1",
        status="completed",
        outputs=[
            {
                "id": "video-task-1",
                "media_type": "video",
                "media_path": "video/video-task-1.mp4",
                "thumbnail_path": "storyboard/frame-1.png",
                "filename": "video-task-1.mp4",
            }
        ],
    )

    row = activity.list_activity("workspace-alice")[0]
    assert row["category"] == "video"
    assert row["parameters"] == {
        "batch_size": 1,
        "duration": 5,
        "generation_mode": "i2v",
        "resolution": "720p",
        "ratio": "16:9",
    }
    assert row["source_context"]["video_task_id"] == "video-task-1"
    assert row["input_media"][0]["media_path"] == "storyboard/frame-1.png"
    assert row["outputs"][0]["media_path"] == "video/video-task-1.mp4"


def test_hybrid_text_activity_persists_model_prompt_and_terminal_state(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    activity.record_text_activity(
        "workspace-alice",
        task_id="text-task-1",
        source_route="#/series/series-1/episode/project-1",
        detail="优化视频提示词",
        prompt="让镜头缓慢推近雨中的霓虹招牌",
        model_name="deepseek-v4-pro",
        source_context={
            "project_id": "project-1",
            "episode_id": "project-1",
            "series_id": "series-1",
            "frame_id": "frame-1",
        },
    )
    activity.update_asset_activity(
        "workspace-alice",
        "text-task-1",
        status="running",
    )
    activity.update_asset_activity(
        "workspace-alice",
        "text-task-1",
        status="completed",
    )

    row = activity.list_activity("workspace-alice")[0]
    assert row["type"] == "chat.completions"
    assert row["category"] == "text"
    assert row["source"] == "workspace"
    assert row["status"] == "completed"
    assert row["detail"] == "优化视频提示词"
    assert row["prompt"] == "让镜头缓慢推近雨中的霓虹招牌"
    assert row["model_name"] == "deepseek-v4-pro"
    assert row["source_context"] == {
        "type": "workspace",
        "route": "#/series/series-1/episode/project-1",
        "project_id": "project-1",
        "episode_id": "project-1",
        "series_id": "series-1",
        "frame_id": "frame-1",
    }
    assert row["finished_at"]


def test_hybrid_storyboard_activity_identifies_frame_and_input(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    activity.record_storyboard_activity(
        "workspace-alice",
        task_id="storyboard-task-1",
        source_route="#/series/series-1/episode/project-1",
        project_id="project-1",
        series_id="series-1",
        frame_id="frame-3",
        detail="3. 灯灯蹲在车头",
        prompt="A brass fox in the rain",
        model_name="gpt-image-2",
        batch_size=2,
        input_media=[
            {
                "id": "input-1",
                "media_type": "image",
                "media_path": "assets/fox.png",
            }
        ],
    )

    row = activity.list_activity("workspace-alice")[0]
    assert row["type"] == "storyboard_render"
    assert row["category"] == "image"
    assert row["parameters"] == {"batch_size": 2}
    assert row["source_context"] == {
        "type": "workspace",
        "route": "#/series/series-1/episode/project-1",
        "asset_id": "frame-3",
        "asset_type": "storyboard_frame",
        "project_id": "project-1",
        "episode_id": "project-1",
        "frame_id": "frame-3",
        "series_id": "series-1",
    }
    assert row["input_media"][0]["media_path"] == "assets/fox.png"


def test_hybrid_asset_activity_marks_restart_orphans_as_failed(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENMOTION_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setattr(activity, "_PROCESS_ID", "process-100")
    activity.record_asset_activity(
        "workspace-alice",
        task_id="task-orphan",
        job_type="project_asset",
        source="workspace",
        source_route="#/project/project-1",
        source_id="project-1",
        series_id=None,
        asset_id="asset-1",
        asset_type="character",
        asset_name="守塔人",
        prompt=None,
        model_name="gpt-image-2",
        batch_size=1,
        aspect_ratio=None,
    )

    monkeypatch.setattr(activity, "_PROCESS_ID", "process-101")
    orphan = activity.list_activity("workspace-alice")[0]
    assert orphan["status"] == "failed"
    assert "重新启动" in orphan["error"]
    assert orphan["finished_at"]


def test_hybrid_asset_outputs_include_only_new_unique_variants() -> None:
    from src.apps.comic_gen.api import _hybrid_asset_outputs

    character = Character(
        id="asset-1",
        name="守塔人",
        description="银白色长发的守塔老人",
    )
    existing = ImageVariant(id="existing", url="assets/existing.png")
    generated = ImageVariant(id="generated", url="assets/generated.png")
    character.reference_sheet.image_variants = [existing, generated]
    character.full_body_asset.variants = [generated]

    assert _hybrid_asset_outputs(character, frozenset({"existing"})) == [
        {
            "id": "generated",
            "media_type": "image",
            "media_path": "assets/generated.png",
            "thumbnail_path": "assets/generated.png",
            "filename": "generated.png",
        }
    ]


def test_hybrid_asset_activity_uses_the_task_owned_canonical_result(
    monkeypatch,
) -> None:
    from src.apps.comic_gen import api as comic_api

    generated = Character(
        id="asset-1",
        name="灯灯",
        description="机械狐狸",
    )
    generated.full_body_asset.variants = [ImageVariant(id="generated", url="assets/generated.png")]

    class RebuiltPipeline:
        def process_asset_generation_task(self, task_id: str) -> None:
            assert task_id == "task-1"

        def get_asset_generation_task_status(self, task_id: str):
            assert task_id == "task-1"
            return {"status": "completed"}

        def asset_generation_task_result_asset(self, task_id: str):
            assert task_id == "task-1"
            return generated

    updates: list[dict] = []
    rebuilt_pipeline = RebuiltPipeline()
    monkeypatch.setattr(
        comic_api,
        "update_asset_activity",
        lambda workspace_id, task_id, **payload: updates.append(
            {"workspace_id": workspace_id, "task_id": task_id, **payload}
        ),
    )

    comic_api._process_hybrid_asset_activity(
        rebuilt_pipeline,
        "task-1",
        "workspace-alice",
        frozenset(),
    )

    assert updates[-1] == {
        "workspace_id": "workspace-alice",
        "task_id": "task-1",
        "status": "completed",
        "outputs": [
            {
                "id": "generated",
                "media_type": "image",
                "media_path": "assets/generated.png",
                "thumbnail_path": "assets/generated.png",
                "filename": "generated.png",
            }
        ],
    }


def test_hybrid_asset_activity_rejects_a_completed_task_without_new_output(
    monkeypatch,
) -> None:
    from src.apps.comic_gen import api as comic_api

    generated = Character(
        id="asset-1",
        name="灯灯",
        description="机械狐狸",
    )

    class PipelineWithoutOutput:
        @staticmethod
        def process_asset_generation_task(task_id: str) -> None:
            assert task_id == "task-1"

        @staticmethod
        def get_asset_generation_task_status(task_id: str):
            assert task_id == "task-1"
            return {"status": "completed"}

        @staticmethod
        def asset_generation_task_result_asset(task_id: str):
            assert task_id == "task-1"
            return generated

    updates: list[dict] = []
    pipeline_without_output = PipelineWithoutOutput()
    monkeypatch.setattr(
        comic_api,
        "update_asset_activity",
        lambda workspace_id, task_id, **payload: updates.append(
            {"workspace_id": workspace_id, "task_id": task_id, **payload}
        ),
    )

    comic_api._process_hybrid_asset_activity(
        pipeline_without_output,
        "task-1",
        "workspace-alice",
        frozenset(),
    )

    assert updates[-1] == {
        "workspace_id": "workspace-alice",
        "task_id": "task-1",
        "status": "failed",
        "error": "素材生成完成，但没有找到新图像输出。",
    }


def test_hybrid_video_activity_resolves_canonical_completed_task(
    monkeypatch,
) -> None:
    from src.apps.comic_gen import api as comic_api

    task = VideoTask(
        id="video-task-1",
        project_id="project-1",
        frame_id="frame-1",
        source_image_url="storyboard/frame-1.png",
        image_url="video_inputs/video-task-1.png",
        prompt="A lantern sways in the rain",
        status="completed",
        video_url="video/video-task-1.mp4",
        model="doubao-seedance-2-0-fast-260128",
        generation_mode="i2v",
    )
    script = Script(
        id="project-1",
        title="灯塔",
        original_text="雨夜里的灯塔。",
        video_tasks=[task],
        created_at=time.time(),
        updated_at=time.time(),
    )

    class RebuiltPipeline:
        def process_video_task(self, script_id: str, task_id: str) -> None:
            assert (script_id, task_id) == ("project-1", "video-task-1")

        def get_script(self, script_id: str):
            assert script_id == "project-1"
            return script

    updates: list[dict] = []
    rebuilt_pipeline = RebuiltPipeline()
    monkeypatch.setattr(
        comic_api,
        "update_asset_activity",
        lambda workspace_id, task_id, **payload: updates.append(
            {"workspace_id": workspace_id, "task_id": task_id, **payload}
        ),
    )

    comic_api._process_hybrid_video_activity(
        rebuilt_pipeline,
        "video-task-1",
        "workspace-alice",
        "project-1",
    )

    assert updates[0]["status"] == "running"
    assert updates[-1] == {
        "workspace_id": "workspace-alice",
        "task_id": "video-task-1",
        "status": "completed",
        "outputs": [
            {
                "id": "video-task-1",
                "media_type": "video",
                "media_path": "video/video-task-1.mp4",
                "thumbnail_path": "storyboard/frame-1.png",
                "filename": "video-task-1.mp4",
            }
        ],
    }


def test_hybrid_storyboard_activity_commits_and_publishes_new_images(monkeypatch) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from src.apps.comic_gen import api as comic_api

    generated = StoryboardFrame(
        id="frame-3",
        scene_id="scene-1",
        action_description="灯灯蹲在车头",
        rendered_image_url="storyboard/generated.png",
        image_url="storyboard/generated.png",
        status="completed",
    )
    generated.rendered_image_asset.variants.append(
        ImageVariant(id="generated", url="storyboard/generated.png")
    )
    generated.rendered_image_asset.selected_id = "generated"
    plan = SimpleNamespace(
        frame_id="frame-3",
        frame=StoryboardFrame(
            id="frame-3",
            scene_id="scene-1",
            action_description="灯灯蹲在车头",
        ),
        existing_variant_ids=frozenset({"existing"}),
    )

    class DetachedPipeline:
        storyboard_generator = SimpleNamespace(output_dir="/tmp/storyboard")

        @staticmethod
        def execute_storyboard_render_plan(received_plan):
            assert received_plan is plan
            return generated

        @staticmethod
        def validate_storyboard_render_result(received_frame):
            assert received_frame is generated

    class CurrentPipeline:
        committed = False

        def commit_storyboard_render_plan(self, received_plan, received_frame):
            assert (received_plan, received_frame) == (plan, generated)
            self.committed = True

    current = CurrentPipeline()

    @contextmanager
    def locked(workspace_id: str):
        assert workspace_id == "workspace-alice"
        yield current

    updates: list[dict] = []
    monkeypatch.setattr(comic_api, "_workspace_pipelines", SimpleNamespace(locked=locked))
    monkeypatch.setattr(
        comic_api,
        "update_asset_activity",
        lambda workspace_id, task_id, **payload: updates.append(
            {"workspace_id": workspace_id, "task_id": task_id, **payload}
        ),
    )

    comic_api._process_hybrid_storyboard_activity(
        "storyboard-task-1",
        "workspace-alice",
        DetachedPipeline(),
        plan,
    )

    assert current.committed is True
    assert updates[0]["status"] == "running"
    assert updates[-1] == {
        "workspace_id": "workspace-alice",
        "task_id": "storyboard-task-1",
        "status": "completed",
        "outputs": [
            {
                "id": "generated",
                "media_type": "image",
                "media_path": "storyboard/generated.png",
                "thumbnail_path": "storyboard/generated.png",
                "filename": "generated.png",
            }
        ],
    }


def test_hybrid_storyboard_activity_preserves_provider_failure_metadata(monkeypatch) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from src.apps.comic_gen import api as comic_api
    from src.models.newapi import NewAPIProviderError

    failure = NewAPIProviderError(
        "暂时无法连接到 AI 服务商。",
        error_code="provider_connection_failed",
        provider_code="provider_connect_failed",
        http_status=502,
        request_id="request-123",
        phase="image submission",
    )
    plan = SimpleNamespace(
        frame_id="frame-4",
        frame=StoryboardFrame(
            id="frame-4",
            scene_id="scene-1",
            action_description="岚转过头",
        ),
        existing_variant_ids=frozenset(),
    )

    class DetachedPipeline:
        storyboard_generator = SimpleNamespace(output_dir="/tmp/storyboard")

        @staticmethod
        def execute_storyboard_render_plan(_received_plan):
            raise failure

        @staticmethod
        def storyboard_render_output_paths(_plan, _frame):
            return []

    class CurrentPipeline:
        @staticmethod
        def fail_storyboard_render_plan(received_plan):
            assert received_plan is plan

    @contextmanager
    def locked(workspace_id: str):
        assert workspace_id == "workspace-alice"
        yield CurrentPipeline()

    updates: list[dict] = []
    monkeypatch.setattr(comic_api, "_workspace_pipelines", SimpleNamespace(locked=locked))
    monkeypatch.setattr(
        comic_api,
        "update_asset_activity",
        lambda workspace_id, task_id, **payload: updates.append(
            {"workspace_id": workspace_id, "task_id": task_id, **payload}
        ),
    )

    comic_api._process_hybrid_storyboard_activity(
        "storyboard-task-failed",
        "workspace-alice",
        DetachedPipeline(),
        plan,
    )

    assert updates[-1]["status"] == "failed"
    assert updates[-1]["error_code"] == "provider_connection_failed"
    assert "HTTP 状态：502" in updates[-1]["error_diagnostic"]
    assert updates[-1]["error"] == "暂时无法连接到 AI 服务商。"
