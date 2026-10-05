from nic.llm import parse_reply


def test_plain_text_reply():
    reply = parse_reply({"message": {"content": "  hello  "}})
    assert reply.text == "hello"
    assert reply.tool_calls == []


def test_tool_call_with_object_arguments():
    reply = parse_reply(
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "android_tap", "arguments": {"x": 10, "y": 20}}}
                ],
            }
        }
    )
    assert reply.tool_calls[0].name == "android_tap"
    assert reply.tool_calls[0].arguments == {"x": 10, "y": 20}


def test_tool_call_with_json_string_arguments():
    reply = parse_reply(
        {"message": {"tool_calls": [{"function": {"name": "t", "arguments": '{"a": 1}'}}]}}
    )
    assert reply.tool_calls[0].arguments == {"a": 1}


def test_malformed_arguments_degrade_to_empty_dict():
    reply = parse_reply(
        {"message": {"tool_calls": [{"function": {"name": "t", "arguments": "not json"}}]}}
    )
    assert reply.tool_calls[0].arguments == {}


def test_nameless_tool_calls_are_skipped():
    reply = parse_reply({"message": {"tool_calls": [{"function": {"arguments": {}}}]}})
    assert reply.tool_calls == []


def test_missing_message_is_handled():
    assert parse_reply({}).text == ""
