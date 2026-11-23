import ClassroomPage from "../pages/ClassroomPage";
import { createBrowserRouter } from "react-router-dom";

import AppShell from "../components/layout/AppShell";
import DocsPage from "../pages/DocsPage";
import ActiveRunsPage from "../pages/ActiveRunsPage";
import AdminExecutorPage from "../pages/AdminExecutorPage";
import AdminPage from "../pages/AdminPage";
import CompetitionDetailPage from "../pages/CompetitionDetailPage";
import CompetitionTaskPage from "../pages/CompetitionTaskPage";
import CreateTaskPage from "../pages/CreateTaskPage";
import HistoryEventPage from "../pages/HistoryEventPage";
import HistoryPage from "../pages/HistoryPage";
import HomePage from "../pages/HomePage";
import LoginPage from "../pages/LoginPage";
import MyTaskDetailPage from "../pages/MyTaskDetailPage";
import MyTasksPage from "../pages/MyTasksPage";
import NotificationsPage from "../pages/NotificationsPage";
import PlaygroundPage from "../pages/PlaygroundPage";
import PlaygroundRequirementDetailPage from "../pages/PlaygroundRequirementDetailPage";
import PlaygroundSubmissionDetailPage from "../pages/PlaygroundSubmissionDetailPage";
import PlaygroundTaskListPage from "../pages/PlaygroundTaskListPage";
import ProfilePage from "../pages/ProfilePage";
import ResearchPage from "../pages/ResearchPage";
import RegisterPage from "../pages/RegisterPage";
import ResetPasswordPage from "../pages/ResetPasswordPage";
import RequirementsPage from "../pages/RequirementsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "login", element: <LoginPage /> },
      { path: "register", element: <RegisterPage /> },
      { path: "reset-password", element: <ResetPasswordPage /> },
      { path: "profile", element: <ProfilePage /> },
      { path: "notifications", element: <NotificationsPage /> },
      { path: "running", element: <ActiveRunsPage /> },
      { path: "competition", element: <RequirementsPage /> },
      { path: "requirements", element: <RequirementsPage /> },
      { path: "docs", element: <DocsPage /> },
      { path: "docs/agent", element: <DocsPage /> },
      { path: "docs/visualization", element: <DocsPage /> },
      { path: "adminarcbench-executor-control", element: <AdminExecutorPage /> },
      { path: "admin", element: <AdminPage /> },
      { path: "competitions/:competitionId", element: <CompetitionDetailPage /> },
      { path: "competitions/:competitionId/tasks/:requirementId", element: <CompetitionTaskPage /> },
      { path: "runs/:submissionId", element: <PlaygroundSubmissionDetailPage /> },
      { path: "requirements/:requirementId", element: <PlaygroundRequirementDetailPage /> },
      { path: "submissions/:submissionId", element: <PlaygroundSubmissionDetailPage /> },
      { path: "playground", element: <PlaygroundPage /> },
      { path: "playground/classroom", element: <ClassroomPage /> },
      { path: "playground/arc-bench/:taskType", element: <PlaygroundTaskListPage /> },
      { path: "playground/arc-bench/:taskType/:requirementId", element: <PlaygroundRequirementDetailPage /> },
      { path: "playground/arc-bench/:taskType/:requirementId/submissions/:submissionId", element: <PlaygroundSubmissionDetailPage /> },
      { path: "playground/task-bank/:taskType", element: <PlaygroundTaskListPage /> },
      { path: "playground/task-bank/:taskType/:requirementId", element: <PlaygroundRequirementDetailPage /> },
      { path: "playground/task-bank/:taskType/:requirementId/submissions/:submissionId", element: <PlaygroundSubmissionDetailPage /> },
      { path: "playground/create-task", element: <CreateTaskPage /> },
      { path: "playground/my-tasks", element: <MyTasksPage /> },
      { path: "playground/my-tasks/:taskId", element: <MyTaskDetailPage /> },
      { path: "playground/my-tasks/:taskId/submissions/:submissionId", element: <PlaygroundSubmissionDetailPage /> },
      { path: "playground/my-tasks/:taskId/edit", element: <CreateTaskPage /> },
      { path: "research", element: <ResearchPage /> },
      { path: "history", element: <HistoryPage /> },
      { path: "history/:eventId", element: <HistoryEventPage /> },
    ],
  },
]);
