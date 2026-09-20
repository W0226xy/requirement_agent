from requirement_agent.application.conversations.intent import ConversationIntent, classify_conversation_message


def test_reporting_phrases_win_over_submission_markers() -> None:
    assert classify_conversation_message("生成当前需求库的需求报告", has_attachment=False) == ConversationIntent.REPORTING_QUERY
    assert classify_conversation_message("汇总当前会话已确认的需求、风险和待确认问题", has_attachment=False) == ConversationIntent.REPORTING_QUERY
    assert classify_conversation_message("生成音乐播放器模块的需求报告", has_attachment=False) == ConversationIntent.REPORTING_QUERY
