from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.translation import ugettext_lazy as _

class DAGCategoryManager(models.Manager):
    def live(self):
        return self.filter(sites__id=settings.SITE_ID)

    def toplevel(self):
        """
        Returns all live categories at the top level
        """
        return self.live().filter(parent__isnull=True)

    def leaf_nodes(self):
        """
        Returns all live categories that have no children
        """
        return self.live().filter(children__isnull=True)

    def inner_nodes(self):
        return self.live().filter(children__isnull=False)

    def select_from_url(self, url, limit=-1):
        """
        Selects a category from a url (delimited by /)
        limit specifies how many extras at the end are allowed, set to 0 for none
        """
        items = url.split('/')
        extras = list()
        if limit != -1:
            limit += 1
        while items and limit != 0:
            path = self.model.DELIMETER.join(items)
            try:
                category = self.live().get(path=path)
            except ObjectDoesNotExist:
                extras.insert(0, items.pop())
            else:
                return category, extras
            limit -= 1
        return None, extras

    def build_tree_structure(self, qs=None):
        """
        Semi-Efficiently builds a python structure representing this tree structure
        Ignores any order you gave the queryset!
        Returns a list containing the root nodes
        All nodes have the following attributes prefetched:
            children_list - a python list of prefetched children
            parent
        """
        if qs is None:
            qs = self.live()
        qs = qs.order_by('path')
        ret = list()
        previous = None
        for node in qs:
            node.children_list = list()
            if node.depth:
                while previous.depth > node.depth: #wind back
                    previous = previous.parent
                if previous.depth == node.depth: #sibling
                    node.parent = previous.parent
                else: #child
                    node.parent = previous
                node.parent.children_list.append(node)
            else:
                ret.append(node)
            previous = node
        return ret

class DAGCategory(models.Model):
    """Django model for categories that allow Directed Acyclic Graph (hierarchical) categorization
    
    The goal is to do the work of updating all category paths on change, and streamline lookup with
    a path column (seperated by colons)
    
    TODO: We should standardize the naming conventions, see http://en.wikipedia.org/wiki/Tree_(data_structure)
    """
    name   = models.CharField(max_length=128)
    slug   = models.SlugField()
    sites  = models.ManyToManyField(Site)
    parent = models.ForeignKey('self', blank=True, null=True, related_name='children', verbose_name=_('Parent Section'), help_text=_("circular hierarchies will be auotomatically prevented"))
    # Magical path that is filled out inefficiently on pre-save
    # This is costly on save, but very effective when selecting parents
    path   = models.CharField(max_length=255, blank=True, unique=True, editable=False)
    order  = models.PositiveIntegerField(default=0)

    objects = DAGCategoryManager()

    DELIMETER = '::'

    def __unicode__(self):
        return u'%s (%s)' % (self.name, self.path)

    @property
    def depth(self):
        """
        0-based length of the path to the root
        """
        return self.path.count(self.DELIMETER)

    def subcategories(self):
        """
        Returns live children
        #TODO: Rename?
        Perhaps active_children?
        This name might be better for all_children
        """
        return self.children.live()

    def subtree(self):
        """
        Returns all nodes below including this one
        """
        return type(self).objects.filter(pk=self.pk) | self.all_children()

    def all_children(self):
        """
        Returns all node below this one
        """
        return type(self).objects.live().filter(path__startswith=self.path+self.DELIMETER)

    def branch(self):
        """
        Returns the entire set of nodes starting with this one, including this one
        """
        qs = type(self).objects.none()
        path_so_far = list()
        for slug in self.path.split(self.DELIMETER):
            path_so_far.append(slug)
            qs |= type(self).objects.filter(path=self.DELIMETER.join(path_so_far))
        return qs.order_by('-path')

    def parents(self):
        """
        Goes from oldest parent to youngest
        """
        return self.travel_up().reverse()

    def travel_up(self):
        """
        Travels up the tree, youngest parent to oldest
        """
        return self.branch().exclude(pk=self.pk)

    @property
    def leaf_node(self):
        """
        True if this is a leaf node (having no children)
        """
        return not bool(self.children.all().count())

    @property
    def inner_node(self):
        return not self.leaf_node

    def save(self, *args, **kwargs):
        # check for and prevent circular parentage, and rebuild path
        path = self.slug
        parents = set((self.pk,))
        p = self
        while p.parent:
            p = p.parent
            assert p.pk not in parents, "Circular Parenting is not allowed"
            path = ''.join((p.slug, self.DELIMETER, path))
            parents.add(p.pk)
        self.path = path
        super(DAGCategory, self).save(*args, **kwargs)
        # then update children (nodes that point to self)
        for c in self.children.all():
            c.update_path(self.path)

    def update_path(self,path_so_far):
        self.path=''.join((path_so_far, self.DELIMETER, self.slug))
        self.save()

    class Meta:
        abstract = True
        ordering = ['order']
        verbose_name = _('category')
        verbose_name_plural = _('categories')

    urllize = lambda self: self.path.replace(self.DELIMETER, '/')

    _all_subitems = lambda self, qs, field: qs.filter(**{field + '__path__startswith': self.path}).distinct()
