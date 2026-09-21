"""An image loader whose explicit fallback is no image.

Why this exists
---------------
ComfyUI's stock LoadImage requires a selected filename. A connected stock
LoadImage with an empty filename fails validation before its output can reach a
node. Qwen-Image 2.1's native reference encoder works differently: every
`images.image_N` input is optional and its implementation explicitly skips
None:

    image = images[name]
    if image is None:
        continue

OptionalLoadImage bridges those two behaviours. Its `[no image]` option returns
`None` through an IMAGE-typed edge, so a permanently connected reference slot
is genuinely absent from both the Qwen3-VL vision sequence and VAE reference
latents. It is not a black/transparent placeholder image.

It deliberately subclasses the stock LoadImage so normal files retain exactly
the stock upload, validation, cache-fingerprint, alpha-mask, animation, and
batch behaviour.
"""

import os

import folder_paths
import nodes


NO_IMAGE = "[no image]"


class OptionalLoadImage(nodes.LoadImage):
    """Load a real image, or yield None for a connected optional-image slot."""

    @classmethod
    def INPUT_TYPES(cls):
        input_dir = folder_paths.get_input_directory()
        # LoadImage itself lists direct image files only (rather than model-style
        # `folder_paths.get_filename_list`, which has no "input" registry key).
        files = [f for f in os.listdir(input_dir)
                 if os.path.isfile(os.path.join(input_dir, f))]
        files = folder_paths.filter_files_content_types(files, ["image"])
        # The list is regenerated whenever ComfyUI asks for node metadata, so
        # images uploaded through the normal widget immediately become choices.
        return {
            "required": {
                "image": ([NO_IMAGE] + sorted(files), {"image_upload": True}),
            }
        }

    CATEGORY = "image"
    ESSENTIALS_CATEGORY = "Image Tools"
    SEARCH_ALIASES = [
        "optional image", "image fallback", "reference image", "load image",
    ]

    def load_image(self, image):
        if image == NO_IMAGE:
            # TextEncodeQwenImage21's autogrow loop explicitly treats this as
            # absent: `if image is None: continue`.
            return (None, None)
        return super().load_image(image)

    @classmethod
    def VALIDATE_INPUTS(cls, image):
        if image == NO_IMAGE:
            return True
        return super().VALIDATE_INPUTS(image)

    @classmethod
    def IS_CHANGED(cls, image):
        # Stable fingerprint while the fallback is selected. Returning NaN or
        # time-based values here would cause needless reruns.
        if image == NO_IMAGE:
            return NO_IMAGE
        return super().IS_CHANGED(image)


NODE_CLASS_MAPPINGS = {
    "OptionalLoadImage": OptionalLoadImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "OptionalLoadImage": "Load Image (Optional / Fallback)",
}
