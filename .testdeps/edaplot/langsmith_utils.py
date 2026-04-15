import uuid

import pandas as pd
from langchain.messages import AIMessage
from langsmith import RunTree
from langsmith.schemas import Attachment

from edaplot.image_utils import encode_image_bytes_to_base64, vl_to_png_bytes
from edaplot.spec_utils import SpecType


def log_spec_as_image(
    run_tree: RunTree,
    spec: SpecType,
    df: pd.DataFrame,
    *,
    scale: float = 1.0,
    attachment_name: str | None = "auto",
    add_to_outputs: bool = True,
) -> bool:
    """Log a Vega-Lite specification as an image in LangSmith."""
    if attachment_name is not None and not add_to_outputs:
        return False

    png_bytes = vl_to_png_bytes(spec, df, scale=scale)
    if png_bytes is None:
        return False

    if attachment_name is not None:
        # See: https://docs.smith.langchain.com/observability/how_to_guides/upload_files_with_traces
        if attachment_name == "auto":
            attachment_name = f"edaplot_{uuid.uuid4()}"
        run_tree.attachments[attachment_name] = Attachment(mime_type="image/png", data=png_bytes)  # type: ignore[assignment]

    if add_to_outputs:
        # Adding images directly as outputs doesn't render correctly in LangSmith.
        # However, LangSmith renders LLM messages with images correctly, so we can add a fake message to the output
        # See: https://docs.smith.langchain.com/observability/how_to_guides/log_multimodal_traces
        b64_bytes = encode_image_bytes_to_base64(png_bytes)
        fake_message = AIMessage(
            content=[
                {"type": "text", "text": "Generated chart:"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64_bytes}",
                    },
                },
            ],
        )
        run_tree.add_outputs({"__fake_messages": [fake_message]})

    return True
