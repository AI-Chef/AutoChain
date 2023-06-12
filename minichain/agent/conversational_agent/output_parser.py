import json
from typing import Union

from colorama import Fore

from minichain.agent.structs import AgentAction, AgentFinish, AgentOutputParser
from minichain.errors import OutputParserException
from minichain.utils import print_with_color


class ConvoJSONOutputParser(AgentOutputParser):
    def parse(self, text: str) -> Union[AgentAction, AgentFinish]:
        try:
            clean_text = text[text.index("{") : text.rindex("}") + 1].strip()
            response = json.loads(clean_text)
        except Exception:
            raise OutputParserException(f"Not a valid json: `{text}`")

        action_name = response.get("tool", {}).get("name")
        action_args = response.get("tool", {}).get("args")

        if (
            "no" in response.get("thoughts", {}).get("need_use_tool").lower().strip()
            or not action_name
        ):
            output_message = response.get("response")
            if output_message:
                return AgentFinish(message=response.get("response"), log=output_message)
            else:
                return AgentFinish(
                    message="Sorry, i don't understand", log=output_message
                )

        return AgentAction(
            tool=action_name,
            tool_input=action_args,
            model_response=response.get("response", ""),
        )

    @staticmethod
    def parse_clarification(
        text: str, agent_action: AgentAction
    ) -> Union[AgentAction, AgentFinish]:
        try:
            clean_text = text[text.index("{") : text.rindex("}") + 1].strip()
            response = json.loads(clean_text)
            print_with_color(f"Full clarification output: {response}", Fore.YELLOW)
        except Exception:
            raise OutputParserException(f"Not a valid json: `{text}`")

        missing_arg_value = response.get("missing_arg_value", "")
        clarifying_question = response.get("clarifying_question", "")

        if "yes" in missing_arg_value.lower() and clarifying_question:
            return AgentFinish(message=clarifying_question, log=clarifying_question)
        else:
            return agent_action
