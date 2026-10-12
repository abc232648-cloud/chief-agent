from deployment.https_policy import contract


def test_shared_origin_routes_do_not_overlap_pwa_authority():
    value=contract('chief.example.com',8765,'127.0.0.1')
    routes={item['path_prefix']:item for item in value['application_routes']}
    assert routes['/api/']['target']=='chief-loopback'
    assert routes['/api/']['cache']=='NEVER'
    assert routes['/owner/']['target']=='owner-static'
    assert routes['/owner/']['service_worker_scope']=='/owner/'
    assert routes['/staff/']['target']=='staff-static'
    assert routes['/staff/']['service_worker_scope']=='/staff/'
    assert routes['/jobs/']['target']=='job-static'
    assert routes['/jobs/']['service_worker_scope']=='/jobs/'
    assert value['root_policy']=='NO_ROOT_SCOPED_PWA'
    assert '/' not in routes
    scopes={item.get('service_worker_scope') for item in routes.values() if item.get('service_worker_scope')}
    assert scopes=={'/owner/','/staff/','/jobs/'}
    requirements=' '.join(value['proxy_requirements'])
    assert '/owner/' in requirements and '/staff/' in requirements and '/jobs/' in requirements
    assert '/sw.js' in requirements and 'scope /' in requirements
