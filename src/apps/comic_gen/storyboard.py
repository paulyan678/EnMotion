import os
import time
from typing import Dict, Any, List
from .models import StoryboardFrame, Character, Scene, Prop, GenerationStatus, ImageAsset, ImageVariant
from ...models.newapi import NewAPIImageModel
from ...utils import get_logger
from ...utils.newapi_models import IMAGE, get_model_spec, get_selected_model
from ...utils.oss_utils import authoritative_media_reference, is_object_key

logger = get_logger(__name__)

class StoryboardGenerator:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.model = NewAPIImageModel(self.config.get('model', {}))
        self.output_root = self.config.get('output_root', 'output')
        self.output_dir = self.config.get(
            'output_dir', os.path.join(self.output_root, 'storyboard')
        )

    def generate_storyboard(self, script: Any, characters: List[Character] = None, scenes: List[Scene] = None) -> Any:
        """Generates images for all frames in the storyboard.

        ``characters`` / ``scenes`` accept the resolver-merged lists
        (Episode → Series → Global). When omitted they fall back to the
        script's own local lists, preserving prior behavior."""
        logger.info(f"Generating storyboard for script: {script.title}")
        resolved_characters = characters if characters is not None else script.characters
        resolved_scenes = scenes if scenes is not None else script.scenes

        total_frames = len(script.frames)
        for i, frame in enumerate(script.frames):
            logger.info(f"Generating frame {i+1}/{total_frames}: {frame.id}")

            # Skip if already completed (unless force regeneration is needed, but for now we skip)
            if frame.status == GenerationStatus.COMPLETED and frame.image_url:
                continue

            # Find scene for this frame
            scene = next((s for s in resolved_scenes if s.id == frame.scene_id), None)

            self.generate_frame(frame, resolved_characters, scene)

        return script

    def generate_frame(self, frame: StoryboardFrame, characters: List[Character], scene: Scene, ref_image_path: str = None, ref_image_paths: List[str] = None, prompt: str = None, batch_size: int = 1, size: str = None, model_name: str = None, exact_request: bool = False) -> StoryboardFrame:
        """Generates a storyboard frame image."""
        selected_model = model_name or get_selected_model(IMAGE)
        get_model_spec(selected_model, IMAGE)
        frame.status = GenerationStatus.PROCESSING

        # Default size for storyboard (landscape)
        effective_size = size or "1536x1024"

        # Construct a rich prompt using character and scene details
        char_descriptions = []

        # Collect reference image paths from assets
        asset_ref_paths = []

        # If frontend provides explicit reference paths, use them directly
        # Otherwise, auto-collect from characters and scene
        use_frontend_refs = exact_request or (ref_image_paths and len(ref_image_paths) > 0) or ref_image_path

        if use_frontend_refs:
            # Use only what frontend provided (already selected by user)
            if ref_image_paths:
                asset_ref_paths.extend(ref_image_paths)
            if ref_image_path:
                asset_ref_paths.append(ref_image_path)
            logger.info(f"[Storyboard] Using {len(asset_ref_paths)} frontend-provided reference images")
        else:
            # Auto-collect from characters and scene (fallback for batch generation)
            for char_id in frame.character_ids:
                char = next((c for c in characters if c.id == char_id), None)
                if char:
                    # Add character reference image - prioritize selected variant from ImageAsset
                    target_url = None
                    source = "none"

                    # Priority 1: Use selected variant from three_view_asset
                    if char.three_view_asset and char.three_view_asset.selected_id:
                        selected_variant = next((v for v in char.three_view_asset.variants if v.id == char.three_view_asset.selected_id), None)
                        if selected_variant:
                            target_url = selected_variant.url
                            source = f"three_view_asset"

                    # Priority 2: Use selected variant from full_body_asset
                    if not target_url and char.full_body_asset and char.full_body_asset.selected_id:
                        selected_variant = next((v for v in char.full_body_asset.variants if v.id == char.full_body_asset.selected_id), None)
                        if selected_variant:
                            target_url = selected_variant.url
                            source = f"full_body_asset"

                    # Priority 3: Use selected variant from headshot_asset
                    if not target_url and char.headshot_asset and char.headshot_asset.selected_id:
                        selected_variant = next((v for v in char.headshot_asset.variants if v.id == char.headshot_asset.selected_id), None)
                        if selected_variant:
                            target_url = selected_variant.url
                            source = f"headshot_asset"

                    # Priority 4: Fallback to legacy fields
                    if not target_url:
                        target_url = char.three_view_image_url or char.full_body_image_url or char.headshot_image_url or char.avatar_url or char.image_url
                        source = "legacy_fields"

                    logger.info(f"[Storyboard] Character '{char.name}' reference: source={source}, url={target_url}")

                    if target_url:
                        if is_object_key(target_url):
                            asset_ref_paths.append(target_url)
                        else:
                            potential_path = os.path.join(self.output_root, target_url)
                            if os.path.exists(potential_path):
                                asset_ref_paths.append(os.path.abspath(potential_path))
                            elif os.path.exists(target_url):
                                asset_ref_paths.append(os.path.abspath(target_url))

            # Add scene reference image
            scene_url = None
            if scene:
                if scene.image_asset and scene.image_asset.selected_id:
                    selected_variant = next((v for v in scene.image_asset.variants if v.id == scene.image_asset.selected_id), None)
                    if selected_variant:
                        scene_url = selected_variant.url
                if not scene_url:
                    scene_url = scene.image_url

                if scene_url:
                    if is_object_key(scene_url):
                        asset_ref_paths.append(scene_url)
                    else:
                        potential_path = os.path.join(self.output_root, scene_url)
                        if os.path.exists(potential_path):
                            asset_ref_paths.append(os.path.abspath(potential_path))
                        elif os.path.exists(scene_url):
                            asset_ref_paths.append(os.path.abspath(scene_url))

        # Collect character descriptions for prompt building
        for char_id in frame.character_ids:
            char = next((c for c in characters if c.id == char_id), None)
            if char:
                char_descriptions.append(f"{char.name} ({char.description})")

        char_text = ", ".join(char_descriptions)

        # Remove duplicates
        asset_ref_paths = list(set(asset_ref_paths))

        if not prompt:
            prompt = f"Storyboard Frame: {frame.action_description}. "
            if char_text:
                prompt += f"Characters: {char_text}. "
            if scene:
                prompt += f"Location: {scene.name}, {scene.description}. "

            prompt += f"Camera: {frame.camera_angle}"
            if frame.camera_movement:
                prompt += f", {frame.camera_movement}"
            prompt += "."
        elif not exact_request:
            # If prompt is provided by user/LLM, ensure character descriptions are still present for I2I consistency
            if char_text and char_text not in prompt:
                prompt = f"{prompt} Characters: {char_text}."

        # Store the optimized prompt
        frame.image_prompt = prompt

        # Initialize rendered_image_asset if not present
        if not frame.rendered_image_asset:
            frame.rendered_image_asset = ImageAsset(asset_id=frame.id, asset_type="storyboard_frame")

        import uuid

        generation_error = None
        generated_count = 0
        for _ in range(batch_size):
            try:
                variant_id = str(uuid.uuid4())
                output_filename = f"{frame.id}_{variant_id}.png"
                output_path = os.path.join(self.output_dir, output_filename)

                # Ensure output directory exists
                os.makedirs(os.path.dirname(output_path), exist_ok=True)


                # Use I2I if reference images are available
                # Pass collected asset paths to model
                logger.info(f"[Storyboard] Calling New API with {len(asset_ref_paths)} reference images")
                self.model.generate(prompt, output_path, ref_image_paths=asset_ref_paths, size=effective_size, model_name=selected_model)

                # Store relative path for frontend serving
                rel_path = os.path.relpath(output_path, self.output_root)

                # Create Variant
                variant = ImageVariant(
                    id=variant_id,
                    url=rel_path,
                    prompt=prompt,
                    created_at=time.time()
                )
                frame.rendered_image_asset.variants.append(variant)
                generated_count += 1

                # Auto-select the latest one
                frame.rendered_image_asset.selected_id = variant_id
            except Exception as e:
                generation_error = e
                logger.error(
                    "Failed to generate frame %s variant %s/%s: %s",
                    frame.id,
                    generated_count + 1,
                    batch_size,
                    e,
                )
                break

        # A batch is useful as soon as it has at least one valid output. Keep
        # successful variants when a later provider call fails instead of
        # discarding the entire (potentially billable) batch.
        selected_variant = next((v for v in frame.rendered_image_asset.variants if v.id == frame.rendered_image_asset.selected_id), None)
        if selected_variant and generated_count > 0:
            frame.rendered_image_url = selected_variant.url
            frame.image_url = selected_variant.url

            frame.updated_at = time.time()
            frame.status = GenerationStatus.COMPLETED
            if generation_error is not None:
                logger.warning(
                    "Storyboard frame %s completed partially with %s/%s variants",
                    frame.id,
                    generated_count,
                    batch_size,
                )

            # Try uploading to OSS if configured - store Object Key (not full URL)
            try:
                from ...utils.oss_utils import OSSImageUploader
                uploader = OSSImageUploader()
                if uploader.is_configured:
                    # Upload the selected variant
                    if selected_variant:
                        # Construct local path from relative path
                        local_path = os.path.join(self.output_root, selected_variant.url)
                        if os.path.exists(local_path):
                            # Upload and get Object Key (not full URL)
                            object_key = uploader.upload_file(
                                local_path,
                                sub_path=f"storyboard"
                            )
                            if object_key:
                                logger.info(f"Uploaded frame {frame.id} to OSS: {object_key}")
                                persisted = authoritative_media_reference(
                                    local_path, self.output_root, object_key
                                )
                                selected_variant.url = persisted
                                frame.rendered_image_url = persisted
                                frame.image_url = persisted
            except Exception as e:
                logger.error(f"Failed to upload frame {frame.id} to OSS: {e}")
                # Continue even if OSS upload fails
        else:
            frame.updated_at = time.time()
            frame.status = GenerationStatus.FAILED
            if generation_error is not None:
                raise generation_error

        return frame
