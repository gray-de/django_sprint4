from django.shortcuts import render, redirect, get_object_or_404
from .models import Category, Post, Comment
from django.utils import timezone
from django.views.generic import (DetailView, ListView,
                                  CreateView, UpdateView, DeleteView)
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.urls import reverse_lazy, reverse
from django import forms
from django.contrib.auth.decorators import login_required
from .forms import CommentForm, PostForm, UserUpdateForm
from django.db.models import Count, Prefetch
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404

User = get_user_model()


class IndexListView(ListView):
    model = Post
    template_name = 'blog/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        paginator = Paginator(
            Post.objects.filter(
                pub_date__lte=timezone.now(),
                is_published=True,
                category__is_published=True
            ).select_related('author', 'location', 'category').order_by(
                '-pub_date').annotate(
                comment_count=Count('post_comments')
            ), 10)
        page_number = self.request.GET.get('page')
        context["page_obj"] = paginator.get_page(page_number)
        return context


class PostDetailView(DetailView):
    model = Post
    template_name = 'blog/detail.html'
    pk_url_kwarg = 'id'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['comments'] = self.object.post_comments.all()
        context['form'] = CommentForm()
        return context

    def get_queryset(self):
        return Post.objects.select_related('author',
                                           'location',
                                           'category').prefetch_related(
            Prefetch('post_comments',
                     queryset=Comment.objects.select_related('author'))
        )

    def get_object(self, queryset=None):
        obj = get_object_or_404(self.get_queryset(),
                                pk=self.kwargs.get(self.pk_url_kwarg))

        if (obj.is_published
            and obj.category.is_published
                and obj.pub_date <= timezone.now()):
            return obj

        if (self.request.user.is_authenticated
                and self.request.user == obj.author):
            return obj

        raise Http404("Пост не найден")


class CategoryPostsListView(ListView):
    model = Post
    template_name = 'blog/category.html'
    slug_field = 'category_slug'
    slug_url_kwarg = 'category_slug'
    paginate_by = 10

    def get_queryset(self):
        category_slug = self.kwargs['category_slug']

        return Post.objects.select_related('author',
                                           'location',
                                           'category').filter(
            pub_date__lte=timezone.now(),
            is_published=True,
            category__is_published=True,
            category__slug=category_slug,
        )

    def get_context_data(self, **kwargs):
        category_slug = self.kwargs['category_slug']
        context = super().get_context_data(**kwargs)
        context['category'] = get_object_or_404(
            Category,
            slug=category_slug,
            is_published=True
        )
        return context


class ProfileDetailView(DetailView):
    model = User
    template_name = 'blog/profile.html'
    slug_field = 'username'
    slug_url_kwarg = 'username'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile'] = self.object
        paginator = Paginator(
            self.object.author_posts.all().order_by('-pub_date').annotate(
                comment_count=Count('post_comments')
            ).select_related('author', 'location', 'category'), 10)
        page_number = self.request.GET.get('page')
        context["page_obj"] = paginator.get_page(page_number)
        context.pop('user', None)
        return context


class PostMixin:
    model = Post
    template_name = 'blog/create.html'
    form_class = PostForm

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['pub_date'].widget = forms.DateInput(
            attrs={'type': 'date'})
        return form

    def form_valid(self, form):
        form.instance.author = self.request.user
        return super().form_valid(form)


class PostCreateView(LoginRequiredMixin,
                     PostMixin,
                     CreateView):
    def get_success_url(self):
        return reverse_lazy('blog:profile',
                            kwargs={'username': self.object.author.username})


class PostUpdateView(PostMixin, UpdateView):
    pk_url_kwarg = 'post_id'

    def get_success_url(self):
        return reverse_lazy('blog:post_detail', kwargs={'id': self.object.id})

    def dispatch(self, request, *args, **kwargs):
        post = self.get_object()
        if (request.user.is_authenticated
                and self.request.user != post.author):
            return redirect('blog:post_detail', id=post.pk)

        if (not request.user.is_authenticated
                and request.method == 'POST'):
            return redirect('blog:post_detail', id=post.pk)

        return super().dispatch(request, *args, **kwargs)


class PostDeleteView(UserPassesTestMixin,
                     LoginRequiredMixin,
                     PostMixin,
                     DeleteView):
    pk_url_kwarg = 'post_id'
    success_url = reverse_lazy('blog:index')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = PostForm(instance=self.object)
        return context

    def test_func(self):
        post = self.get_object()
        return self.request.user == post.author


@login_required
def add_comment(request, post_id):
    post = get_object_or_404(Post, pk=post_id)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.author = request.user
        comment.post = post
        comment.save()
    return redirect('blog:post_detail', id=post_id)


@login_required
def edit_comment(request, post_id, comment_id):

    comment = get_object_or_404(
        Comment.objects.select_related('post'),
        pk=comment_id,
        post_id=post_id
    )

    post = comment.post

    if comment.author != request.user:
        raise PermissionDenied("Вы не можете редактировать чужой комментарий.")

    if request.method == 'POST':
        form = CommentForm(request.POST, instance=comment)
        if form.is_valid():
            form.save()

            return redirect('blog:post_detail', id=post_id)
    else:
        form = CommentForm(instance=comment)

    context = {'form': form, 'post': post, 'comment': comment}
    return render(request, 'blog/comment.html', context)


@login_required
def delete_comment(request, post_id, comment_id):
    comment = get_object_or_404(
        Comment.objects.select_related('post'),
        pk=comment_id,
        post_id=post_id
    )

    post = comment.post
    if comment.author != request.user:
        raise PermissionDenied("Вы не можете редактировать чужой комментарий.")
    if request.method == 'POST':
        comment.delete()
        return redirect('blog:post_detail', id=post_id)

    return render(request,
                  'blog/comment.html',
                  {'post': post, 'comment': comment})


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = 'blog/user.html'

    def get_object(self, queryset=None):
        return self.request.user

    def get_success_url(self):
        return reverse('blog:profile', args=[self.request.user.username])


class CustomLoginView(LoginView):
    def get_success_url(self):
        return reverse('blog:profile',
                       kwargs={'username': self.request.user.username})
