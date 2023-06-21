from __future__ import annotations

import json
import logging
from string import Template
from typing import Any, Dict, List, Optional, Union

from colorama import Fore
from autochain.agent.base_agent import BaseAgent
from autochain.agent.conversational_agent.output_parser import ConvoJSONOutputParser
from autochain.agent.conversational_agent.prompt import (
    CLARIFYING_QUESTION_PROMPT,
    PLANNING_PROMPT,
)
from autochain.agent.message import BaseMessage, ChatMessageHistory
from autochain.agent.prompt_formatter import JSONPromptTemplate
from autochain.agent.structs import AgentAction, AgentFinish
from autochain.models.base import BaseLanguageModel, Generation
from autochain.tools.base import Tool
from autochain.utils import print_with_color

logger = logging.getLogger(__name__)


class ConversationalAgent(BaseAgent):
    """
    Simple conversational agent who can use tools available to make a conversation by following
    the conversational planning prompt
    """

    output_parser: ConvoJSONOutputParser = ConvoJSONOutputParser()
    llm: BaseLanguageModel = None
    prompt_template: JSONPromptTemplate = None
    allowed_tools: Dict[str, Tool] = {}
    tools: List[Tool] = []

    @classmethod
    def from_llm_and_tools(
        cls,
        llm: BaseLanguageModel,
        tools: Optional[List[Tool]] = None,
        output_parser: Optional[ConvoJSONOutputParser] = None,
        prompt: str = PLANNING_PROMPT,
        input_variables: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ConversationalAgent:
        """Construct an agent from an LLM and tools."""
        tools = tools or []

        prompt_template = cls.get_prompt_template(
            prompt=prompt,
            input_variables=input_variables,
        )

        allowed_tools = {tool.name: tool for tool in tools}
        _output_parser = output_parser or ConvoJSONOutputParser()
        return cls(
            llm=llm,
            allowed_tools=allowed_tools,
            output_parser=_output_parser,
            prompt_template=prompt_template,
            tools=tools,
            **kwargs,
        )

    @staticmethod
    def format_prompt(
        template: JSONPromptTemplate,
        intermediate_steps: List[AgentAction],
        **kwargs: Any,
    ) -> List[BaseMessage]:
        def _construct_scratchpad(
            actions: List[AgentAction],
        ) -> Union[str, List[BaseMessage]]:
            scratchpad = ""
            for action in actions:
                scratchpad += action.response
            return scratchpad

        """Create the full inputs for the LLMChain from intermediate steps."""
        thoughts = _construct_scratchpad(intermediate_steps)
        new_inputs = {"agent_scratchpad": thoughts}
        full_inputs = {**kwargs, **new_inputs}
        prompt = template.format_prompt(**full_inputs)
        return prompt

    @staticmethod
    def get_prompt_template(
        prompt: str = "",
        input_variables: Optional[List[str]] = None,
    ) -> JSONPromptTemplate:
        """Create prompt in the style of the zero shot agent.

        Args:
            prompt: message to be injected between prefix and suffix.
            input_variables: List of input variables the final prompt will expect.

        Returns:
            A PromptTemplate with the template assembled from the pieces here.
        """
        template = Template(prompt)

        if input_variables is None:
            input_variables = ["input", "agent_scratchpad"]
        return JSONPromptTemplate(template=template, input_variables=input_variables)

    def plan(
        self,
        history: ChatMessageHistory,
        intermediate_steps: List[AgentAction],
        **kwargs: Any
    ) -> Union[AgentAction, AgentFinish]:
        """
        Plan the next step. either taking an action with AgentAction or respond to user with AgentFinish
        Args:
            intermediate_steps: List of AgentAction that has been performed with outputs
            **kwargs: key value pairs from chain, which contains query and other stored memories

        Returns:
            AgentAction or AgentFinish
        """
        print_with_color("Planning", Fore.LIGHTYELLOW_EX)
        tool_names = ", ".join([tool.name for tool in self.tools])
        tool_strings = "\n\n".join(
            [f"> {tool.name}: \n{tool.description}" for tool in self.tools]
        )
        inputs = {"tool_names": tool_names,
                  "tools": tool_strings,
                  "history": history.format_message(),
                  **kwargs}
        final_prompt = self.format_prompt(
            self.prompt_template, intermediate_steps, **inputs
        )
        logger.info(f"\nFull Input: {final_prompt[0].content} \n")

        full_output: Generation = self.llm.generate(final_prompt).generations[0]
        agent_output: Union[AgentAction, AgentFinish] = self.output_parser.parse(
            full_output.message
        )

        print_with_color(
            f"Full output: {json.loads(full_output.message.content)}", Fore.YELLOW
        )
        if isinstance(agent_output, AgentAction):
            print_with_color(
                f"Plan to take action '{agent_output.tool}'", Fore.LIGHTYELLOW_EX
            )

        return agent_output

    def clarify_args_for_agent_action(
        self,
        agent_action: AgentAction,
        history: ChatMessageHistory,
        intermediate_steps: List[AgentAction],
        **kwargs: Any,
    ):
        """
        Ask clarifying question if needed. When agent is about to perform an action, we could
        use this function with different prompt to ask clarifying question for input if needed.
        Sometimes the planning response would already have the clarifying question, but we found
        it is more precise if there is a different prompt just for clarifying args

        Args:
            agent_action: agent action about to take
            history: conversation history including the latest query
            intermediate_steps: list of agent action taken so far
            **kwargs:

        Returns:
            Either a clarifying question (AgentFinish) or take the planned action (AgentAction)
        """
        print_with_color("Deciding if need clarification", Fore.LIGHTYELLOW_EX)
        if not self.allowed_tools.get(agent_action.tool):
            return agent_action
        else:
            inputs = {
                "tool_name": agent_action.tool,
                "tool_desp": self.allowed_tools.get(agent_action.tool).description,
                "history": history.format_message(),
                **kwargs,
            }

            clarifying_template = self.get_prompt_template(
                prompt=CLARIFYING_QUESTION_PROMPT
            )

            final_prompt = self.format_prompt(
                clarifying_template, intermediate_steps, **inputs
            )
            logger.info(f"\nClarification inputs: {final_prompt[0].content}")
            full_output: Generation = self.llm.generate(final_prompt).generations[0]
            return self.output_parser.parse_clarification(
                full_output.message, agent_action=agent_action
            )