# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Provie a Dynamic View.

A dynamic diagram can be useful when you want to show how elements in a static model
collaborate at runtime to implement a user story, use case, feature, etc. This dynamic
diagram is based upon a UML communication diagram (previously known as a "UML
collaboration diagram"). It is similar to a UML sequence diagram although it allows a
free-form arrangement of diagram elements with numbered interactions to indicate
ordering.
"""

from contextlib import contextmanager
from typing import Optional, Tuple, Union

from pydantic import Field

from ..mixin.model_ref_mixin import ModelRefMixin
from ..model import Component, Container, Element, Person, Relationship, SoftwareSystem
from ..model.static_structure_element import StaticStructureElement
from .relationship_view import RelationshipView
from .sequence_number import SequenceNumber
from .view import View, ViewIO


__all__ = ("DynamicView", "DynamicViewIO")


class DynamicViewIO(ViewIO):
    """
    Represent the dynamic view from the C4 model.

    Attributes:
        element: The software system or container that this view is focused on.
    """

    element_id: Optional[str] = Field(default=None, alias="elementId")


class DynamicView(ModelRefMixin, View):
    """
    Represent the dynamic view from the C4 model.

    Attributes:
        element: The software system or container that this view is focused on.
    """

    def __init__(
        self,
        *,
        software_system: Optional[SoftwareSystem] = None,
        container: Optional[Container] = None,
        **kwargs,
    ) -> None:
        """Initialize a DynamicView.

        Note that we explicitly don't pass the software_system to the superclass as we
        don't want it to appear in the JSON output (DynamicView uses elementId
        instead).
        """
        if software_system is not None and container is not None:
            raise ValueError("You cannot specify both software_system and container")
        super().__init__(**kwargs)
        self.element = software_system or container
        self.element_id = self.element.id if self.element else None
        self.sequence_number = SequenceNumber()

    def add(
        self,
        source: Element,
        destination: Element,
        description: Optional[str] = None,
        *,
        technology: Optional[str] = None,
    ) -> RelationshipView:
        """Add a relationship to this DynamicView.

        This will search for a relationship in the model from the source to the
        destination with matching description and technology (if specified).  It
        will also look for situations where this interaction is a "response" in
        that it it goes in the opposite direction to the relationship in the
        model, and in this case then description is ignored for matching but will
        appear in the view.

        Example:
            dynamic_view.add(container1, "Requests data from", container2)
            dynamic_view.add(container2, "Sends response back to" container1)
        """
        self.check_element_can_be_added(source)
        self.check_element_can_be_added(destination)
        relationship, response = self._find_relationship(
            source, description, destination, technology
        )
        if relationship is None:
            if technology:
                raise ValueError(
                    f"A relationship between {source.name} and "
                    f"{destination.name} with technology "
                    f"'{technology}' does not exist in model."
                )
            else:
                raise ValueError(
                    f"A relationship between {source.name} and "
                    f"{destination.name} does not exist in "
                    "model."
                )
        self._add_element(source, False)
        self._add_element(destination, False)
        return self._add_relationship(
            relationship,
            description=description or relationship.description,
            order=self.sequence_number.get_next(),
            response=response,
        )

    @contextmanager
    def parallel_sequence(self, continue_numbering: bool):
        r"""
        Start a parallel sequence through a `with` block.

        Args:
            continue_numbering: if `True` then when the with block completes, the main
                                sequence number will continue from after the last
                                number from the parallel sequence.  If `False` then it
                                will reset back to the start (usually so you can start
                                a new parallel sequence).

        Parallel sequences allow for multiple parallel flows to share the same
        sequence numbers, so e.g.
                   /-> C -\
          A -> B -{        }-> E -> F
                   \-> D -/
        could happen concurrently but you want both B->C and B->D to get order
        number 2, and C->E and D->E to get order number 3.  To achieve this,
        you would do:

            dynamic_view.add(a, b)      # Will be order "1"
            with dynamic_view.parallel_sequence(False):
                dynamic_view.add(b, c)  # "2"
                dynamic_view.add(c, e)  # "3"
            with dynamic_view.parallel_sequence(True):
                dynamic_view.add(b, d)  # "2" again
                dynamic_view.add(d, e)  # "3"
            dynamiic_view.add(e, f)     # "4"
        """
        try:
            self.sequence_number.start_parallel_sequence()
            yield self
        finally:
            self.sequence_number.end_parallel_sequence(continue_numbering)

    def check_element_can_be_added(self, element: Element) -> None:
        if not isinstance(element, StaticStructureElement):
            raise ValueError(
                "Only people, software systems, containers and components can be "
                "added to dynamic views."
            )
        if isinstance(element, Person):
            return

        if isinstance(self.element, SoftwareSystem):
            # System scope, so only systems and containers are allowed
            if element is self.element:
                raise ValueError(
                    f"{element.name} is already the scope of this view and cannot be "
                    "added to it."
                )
            if isinstance(element, Component):
                raise ValueError(
                    "Components can't be added to a dynamic view when the scope is a "
                    "software system"
                )
            self.check_parent_and_children_not_in_view(element)
        elif isinstance(self.element, Container):
            # Container scope
            if element is self.element or element is self.element.parent:
                raise ValueError(
                    f"{element.name} is already the scope of this view and cannot be "
                    "added to it."
                )
            self.check_parent_and_children_not_in_view(element)
        else:
            # No scope - only systems can be added
            assert self.element is None
            if not isinstance(element, SoftwareSystem):
                raise ValueError(
                    "Only people and software systems can be added to this dynamic "
                    "view."
                )

    def _find_relationship(
        self,
        source: Element,
        description: str,
        destination: Element,
        technology: Optional[str],
    ) -> Tuple[Optional[Relationship], bool]:
        rel = next(
            (
                rel
                for rel in source.get_efferent_relationships()
                if rel.destination is destination
                and (rel.description == description or not description)
                and (rel.technology == technology or technology is None)
            ),
            None,
        )
        if rel:
            return rel, False

        # Look for "response" to relationship in the opposite direction but ignore
        # descriptions
        rel = next(
            (
                rel
                for rel in source.get_afferent_relationships()
                if rel.source is destination
                and (rel.technology == technology or technology is None)
            ),
            None,
        )
        return rel, True

    @classmethod
    def hydrate(
        cls, io: DynamicViewIO, *, element: Optional[Union[SoftwareSystem, Container]]
    ) -> "DynamicView":
        """Hydrate a new DynamicView instance from its IO."""
        system = element if isinstance(element, SoftwareSystem) else None
        container = element if isinstance(element, Container) else None
        return cls(
            software_system=system,
            container=container,
            **cls.hydrate_arguments(io),
        )