import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path

import httpx

from long_video_studio.adapters.image_edit import (
    ImageEditReference,
    ImageEditRequest,
    OpenAICompatibleImageEditProvider,
    build_first_frame_prompt,
    build_reference_manifest,
    known_multi_image_support,
    provider_from_settings,
)

PNG = b"\x89PNG\r\n\x1a\nreference"
EDITED = b"\x89PNG\r\n\x1a\ncomposited"


def test_reference_manifest_preserves_roles_and_order(tmp_path: Path):
    refs = (
        ImageEditReference(tmp_path / "scene.png", "old town", "location", ("street",)),
        ImageEditReference(
            tmp_path / "hero.png",
            "hero",
            "character",
            ("red coat", "woman"),
            "the lead character",
        ),
    )

    manifest = build_reference_manifest(refs)

    assert manifest.index("[1]") < manifest.index("[2]")
    assert "role=location" in manifest
    assert "role=character" in manifest
    assert "red coat, woman" in manifest


def test_first_frame_prompt_binds_named_references_and_single_instant(tmp_path: Path):
    refs = (
        ImageEditReference(
            tmp_path / "palace.png",
            "太和殿",
            "location",
            ("宫殿",),
            "宽阔的宫殿广场和金色屋顶",
        ),
        ImageEditReference(tmp_path / "bai-lu.png", "白鹿", "character", ("角色",)),
        ImageEditReference(tmp_path / "meng-zi-yi.png", "孟子义", "character", ("角色",)),
    )

    prompt = build_first_frame_prompt(
        refs,
        "两位角色在宫殿前克制地对话，镜头从中远景开始。",
        "16:9",
    )

    assert "太和殿" in prompt
    assert "白鹿" in prompt
    assert "孟子义" in prompt
    assert "宽阔的宫殿广场和金色屋顶" in prompt
    assert "场景/背景" in prompt
    assert "角色身份" in prompt
    assert "一个明确的时间瞬间" in prompt
    assert "仅按序号称呼参考图" in prompt
    assert "不能按名字的字面含义改画成动物" in prompt


def test_openai_compatible_provider_sends_multimodal_manifest_and_writes_image(tmp_path: Path):
    scene = tmp_path / "scene.png"
    hero = tmp_path / "hero.png"
    scene.write_bytes(PNG)
    hero.write_bytes(PNG)
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        encoded = base64.b64encode(EDITED).decode("ascii")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleImageEditProvider(
        "http://image-edit.test",
        "Qwen-Image-Edit-2509",
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    output = tmp_path / "anchor.png"
    request = ImageEditRequest(
        prompt="Place both references in one cinematic street scene.",
        references=(
            ImageEditReference(scene, "old town", "location"),
            ImageEditReference(hero, "hero", "character", ("red coat",)),
        ),
        output_path=output,
        width=1280,
        height=720,
        extra_body={"num_inference_steps": 50},
    )

    result = asyncio.run(provider.edit(request))

    body = captured["body"]
    assert captured["url"] == "http://image-edit.test/v1/chat/completions"
    assert body["model"] == "Qwen-Image-Edit-2509"
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "role=location" in content[0]["text"]
    assert "role=character" in content[0]["text"]
    assert [item["type"] for item in content[1:]] == ["image_url", "image_url"]
    assert body["extra_body"] == {
        "width": 1280,
        "height": 720,
        "num_inference_steps": 50,
    }
    assert result == output
    assert output.read_bytes() == EDITED


def test_vllm_omni_images_edits_protocol_sends_all_reference_files(tmp_path: Path):
    references = []
    for name in ("scene.png", "bai-lu.png", "meng-zi-yi.png"):
        path = tmp_path / name
        path.write_bytes(PNG + name.encode())
        references.append(ImageEditReference(path, name, "reference"))
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(EDITED).decode("ascii")}]},
        )

    provider = OpenAICompatibleImageEditProvider(
        "http://image-edit.test",
        "Qwen-Image-Edit-2511",
        protocol="images-edits",
        transport=httpx.MockTransport(handler),
    )
    output = tmp_path / "anchor.png"
    request = ImageEditRequest(
        prompt="Compose both actors in the scene.",
        references=tuple(references),
        output_path=output,
        width=1280,
        height=720,
        negative_prompt=" ",
        extra_body={"num_inference_steps": 40, "true_cfg_scale": 4.0},
    )

    result = asyncio.run(provider.edit(request))

    body = captured["body"]
    assert captured["url"] == "http://image-edit.test/v1/images/edits"
    assert str(captured["content_type"]).startswith("multipart/form-data; boundary=")
    assert body.count(b'name="image"') == 3
    assert b'name="num_inference_steps"' in body
    assert b"\r\n\r\n40\r\n" in body
    assert b"bai-lu.png" in body
    assert b"meng-zi-yi.png" in body
    assert result == output
    assert output.read_bytes() == EDITED


def test_provider_enforces_reference_cap(tmp_path: Path):
    image = tmp_path / "image.png"
    image.write_bytes(PNG)
    provider = OpenAICompatibleImageEditProvider("http://test", "model", max_references=1)
    request = ImageEditRequest(
        prompt="compose",
        references=(
            ImageEditReference(image, "one", "character"),
            ImageEditReference(image, "two", "character"),
        ),
        output_path=tmp_path / "out.png",
    )

    try:
        provider.build_payload(request)
    except ValueError as exc:
        assert "at most 1" in str(exc)
    else:
        raise AssertionError("reference cap was not enforced")


def test_provider_reads_official_vllm_omni_content_shape_and_routes_negative_prompt(tmp_path: Path):
    image = tmp_path / "image.png"
    image.write_bytes(PNG)
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        encoded = base64.b64encode(EDITED).decode("ascii")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                                }
                            ]
                        }
                    }
                ]
            },
        )

    provider = OpenAICompatibleImageEditProvider(
        "http://image-edit.test/v1",
        "Qwen/Qwen-Image-Edit-2509",
        transport=httpx.MockTransport(handler),
    )
    output = tmp_path / "anchor.png"
    request = ImageEditRequest(
        prompt="Keep the hero in the scene.",
        references=(ImageEditReference(image, "hero", "character"),),
        output_path=output,
        negative_prompt="text, logo, watermark",
    )

    asyncio.run(provider.edit(request))

    body = captured["body"]
    assert body["extra_body"]["negative_prompt"] == "text, logo, watermark"
    assert "NEGATIVE PROMPT" not in body["messages"][0]["content"][0]["text"]
    assert output.read_bytes() == EDITED


def test_known_qwen_checkpoint_capabilities_are_fail_closed(settings):
    assert known_multi_image_support("Qwen/Qwen-Image-Edit") is False
    assert known_multi_image_support("Qwen/Qwen-Image-Edit-2509") is True
    assert known_multi_image_support("vendor/custom-edit-model") is None

    configured = replace(
        settings,
        image_edit_provider="vllm-omni",
        image_edit_base_url="http://image-edit.test",
        image_edit_model="Qwen/Qwen-Image-Edit",
        image_edit_max_references=4,
    )
    try:
        provider_from_settings(configured)
    except ValueError as exc:
        assert "accepts one input image" in str(exc)
    else:
        raise AssertionError("single-image checkpoint accepted a multi-image limit")

    multi_image = replace(
        settings,
        image_edit_provider="vllm-omni",
        image_edit_base_url="http://image-edit.test",
        image_edit_model="Qwen/Qwen-Image-Edit-2511",
        image_edit_max_references=4,
    )
    provider = provider_from_settings(multi_image)
    assert provider.capabilities.protocol == "images-edits-multipart"
