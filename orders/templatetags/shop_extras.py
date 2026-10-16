from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Template filter to get dict item by variable key."""
    return dictionary.get(key, [])