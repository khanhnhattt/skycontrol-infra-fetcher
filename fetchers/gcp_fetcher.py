import os


def fetch_gcp():
    from google.cloud import resourcemanager_v3

    try:
        client = resourcemanager_v3.ProjectsClient()
        project_id = os.getenv("GCP_PROJECT_ID")
        request = resourcemanager_v3.GetProjectRequest(name=f"projects/{project_id}")
        project = client.get_project(request=request)

        return {
            "gcp_inventory": {
                "project_id": project.project_id,
                "display_name": project.display_name,
                "state": str(project.state),
            }
        }
    except Exception as e:
        return {"error": f"GCP fetch failed: {str(e)}"}
