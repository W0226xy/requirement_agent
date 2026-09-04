from datetime import UTC, datetime

import pytest

from requirement_agent.connectors.document import DocumentConnector
from requirement_agent.connectors.image import ImageConnector
from requirement_agent.connectors.web_form import WebFormConnector
from requirement_agent.domain.sources.entities import (
    AttachmentInput,
    FileConnectorRequest,
    WebFormConnectorRequest,
)
from requirement_agent.shared.enums import ChannelType


@pytest.mark.parametrize(
    ("connector", "input_request", "expected_channel", "attachment_count"),
    [
        (
            WebFormConnector(),
            WebFormConnectorRequest(
                external_event_id="event-web-1",
                submitter_id="user-1",
                submitter_name="Tester",
                raw_text="Add export support",
                received_at=datetime.now(UTC),
            ),
            ChannelType.WEB_FORM,
            0,
        ),
        (
            DocumentConnector(),
            FileConnectorRequest(
                external_event_id="event-doc-1",
                submitter_id="user-1",
                submitter_name="Tester",
                received_at=datetime.now(UTC),
                attachment=AttachmentInput(
                    file_name="requirement.pdf",
                    file_type="application/pdf",
                    content=b"%PDF-1.7",
                ),
            ),
            ChannelType.DOCUMENT,
            1,
        ),
        (
            ImageConnector(),
            FileConnectorRequest(
                external_event_id="event-image-1",
                submitter_id="user-1",
                submitter_name="Tester",
                received_at=datetime.now(UTC),
                attachment=AttachmentInput(
                    file_name="screenshot.png",
                    file_type="image/png",
                    content=b"\x89PNG\r\n\x1a\n",
                ),
            ),
            ChannelType.IMAGE,
            1,
        ),
    ],
)
async def test_connector_contract(
    connector: object,
    input_request: object,
    expected_channel: ChannelType,
    attachment_count: int,
) -> None:
    assert await connector.verify(input_request)
    source = await connector.receive(input_request)
    attachments = await connector.download_attachments(source)

    assert source.channel_type == expected_channel
    assert len(attachments) == attachment_count
